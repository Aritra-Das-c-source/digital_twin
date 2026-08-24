#include "Simulation.hpp"

#include <algorithm>
#include <cmath>

Simulation::Simulation()
    : rng(42), sensorRng(202), checkpointRng(4242), output("output/run_001")
{
    initializeFactory();
    initializeCheckpointConfig();
    output.writeStations(stations);
    output.writeStationCheckpoints(checkpoints);
}

void Simulation::initializeFactory() {
    // The layout deliberately mixes automation, manual work and inspections.
    // Wear and latent defects remain simulation-only state; analytics sees only
    // the schema-v1 observable data emitted below.
    stations.clear();
    constexpr std::size_t stationCount = 40;
    for (std::size_t i = 0; i < stationCount; ++i) {
        const bool manual = i == 6 || i == 13 || i == 20 || i == 27 || i == 34;
        const bool inspection = i == 9 || i == 19 || i == 29 || i == 39;
        const std::string archetype = inspection ? "INSPECTION" :
            (manual ? "MANUAL" : "AUTOMATED");
        const std::string coverage = manual ? "NONE" :
            ((i % 4 == 0 || inspection) ? "PARTIAL" : "HIGH");
        const Time cycleTime = manual ? 60000 :
            (inspection ? 50000 : 38000 + static_cast<Time>((i % 6) * 4500));

        stations.push_back({
            static_cast<StationId>(i),
            "Station_" + std::to_string(i + 1),
            archetype,
            cycleTime,
            manual ? 0.18 : 0.10,
            coverage,
            i == 0,
            i == stationCount - 1,
            StationState::IDLE,
            i == 0 ? 0U : (manual ? 2U : 4U)
        });
    }
    degradation.assign(stations.size(), {});
    sensorStates.assign(stations.size(), {});

    // These profiles are hidden scenario configuration. Analytics can only
    // observe their consequences through performance and measurement data.
    degradation[6].scenario = DegradationScenario::GRADUAL;
    degradation[16].scenario = DegradationScenario::ACCELERATING; // S17
    degradation[23].scenario = DegradationScenario::STEP;
    degradation[28].scenario = DegradationScenario::INTERMITTENT;
    degradation[33].scenario = DegradationScenario::SEVERE;
    degradation[33].level = 0.12;
}

void Simulation::initializeCheckpointConfig() {
    checkpoints.clear();
    for (const Station& station : stations) {
        if (station.archetype != "MANUAL") continue;
        checkpoints.push_back({station.id, "CP1", "RFID", 0.50, 0.88, 0.02});
        checkpoints.push_back({station.id, "CP2", "POWER_DRAW", 0.75, 0.85, 0.02});
    }
}

Time Simulation::sampleCycleTime(const Station& station) {
    if (station.meanCycleTime == 0) {
        return 0;
    }

    const DegradationState& state = degradation[station.id];
    const double severity = performanceSeverity(station);

    // Low degradation is mostly invisible in throughput. As degradation
    // worsens, both the expected cycle time and its variability rise; rare
    // long cycles model non-linear operational symptoms rather than a direct
    // wear-to-cycle-time conversion.
    double meanMultiplier = 1.0 + 0.45 * severity * severity;
    if (state.intermittentFaultActive) meanMultiplier += 0.12;
    if (state.scenario == DegradationScenario::SEVERE) meanMultiplier += 0.08 * severity;
    const double effectiveMean = station.meanCycleTime * meanMultiplier;
    const double cv = station.cycleTimeCV *
        (1.0 + 1.4 * severity + (state.intermittentFaultActive ? 0.55 : 0.0));

    double sigma = std::sqrt(
        std::log(1.0 + cv * cv)
    );

    double mu =
        std::log(
            effectiveMean
        )
        - (sigma * sigma) / 2.0;

    std::lognormal_distribution<double> distribution(mu, sigma);

    double sampled = distribution(rng);

    const double outlierProbability = 0.06 * severity * severity +
        (state.intermittentFaultActive ? 0.14 : 0.0);
    std::bernoulli_distribution longCycle(outlierProbability);
    if (longCycle(rng)) {
        std::uniform_real_distribution<double> outlierFactor(1.25, 2.10);
        sampled *= outlierFactor(rng);
    }

    return std::max<Time>(
        1,
        static_cast<Time>(std::round(sampled))
    );
}

bool Simulation::canAcceptUnit(const Station& station) const {
    return station.buffer.size() < station.bufferCapacity;
}

void Simulation::tryUnblockUpstream(Station& station) {
    if (station.isSource) return;

    Station& upstream = stations[station.id - 1];
    if (upstream.state != StationState::BLOCKED) return;
    if (!upstream.currentUnit.has_value()) return;
    if (!canAcceptUnit(station)) return;

    UnitId unitId = *upstream.currentUnit;

    station.buffer.push(unitId);

    upstream.currentUnit.reset();
    upstream.state = StationState::IDLE;

    output.writeStationEvent({
        currentTime,
        StationEventType::UNIT_ARRIVED,
        station.id,
        unitId,
        station.buffer.size(),
        std::nullopt,
        std::nullopt,
        std::nullopt
    });

    tryStartProcessing(upstream);
}

void Simulation::run(Time until) {
    tryStartProcessing(stations[0]);

    // Sensor telemetry is sampled independently of unit-processing events.
    // This preserves idle, blocked and starved machine signatures as well as
    // the higher-load signal during processing.
    for (const Station& station : stations) {
        if (station.sensorCoverage != "NONE") {
            scheduleEvent({0, SimEventType::SENSOR_SAMPLE, station.id, 0});
        }
    }

    while (!events.empty()) {
        SimEvent event = events.top();
        events.pop();

        if (event.timestamp > until) break;

        currentTime = event.timestamp;
        handleEvent(event);
    }

    flushCheckpointEvents(until);
    output.writeRunMetadata("run_001", 42, until, stations.size(), nextUnitId - 1);
}

void Simulation::scheduleEvent(const SimEvent& event) {
    events.push(event);
}

void Simulation::handleEvent(const SimEvent& event) {
    switch (event.type) {
        case SimEventType::PROCESSING_COMPLETE:
            handleProcessingComplete(event);
            break;
        case SimEventType::SENSOR_SAMPLE:
            handleSensorSample(event);
            break;
    }
}

UnitId Simulation::createUnit() {
    UnitId id = nextUnitId++;
    const bool higherRiskBatch = id % 7 == 0;
    std::bernoulli_distribution initialDefect(higherRiskBatch ? 0.07 : 0.015);
    latentDefects[id] = initialDefect(rng);
    output.writeUnit(id, currentTime);
    return id;
}

void Simulation::advanceDegradation(const Station& station) {
    DegradationState& state = degradation[station.id];
    ++state.completedCycles;
    std::normal_distribution<double> variation(0.0, 0.00004);
    double increment = 0.00002;

    switch (state.scenario) {
        case DegradationScenario::HEALTHY:
            increment = 0.00002;
            state.intermittentFaultActive = false;
            break;
        case DegradationScenario::GRADUAL:
            increment = 0.00022;
            break;
        case DegradationScenario::ACCELERATING:
            increment = 0.00007 + std::max<std::int64_t>(0,
                static_cast<std::int64_t>(state.completedCycles) - 70) * 0.000007;
            break;
        case DegradationScenario::STEP:
            increment = 0.00010;
            if (state.completedCycles >= 150) {
                state.level = std::max(state.level, 0.42);
                increment = 0.00032;
            }
            break;
        case DegradationScenario::INTERMITTENT: {
            increment = 0.00010;
            std::bernoulli_distribution faultOccurs(0.06 + 0.12 * state.level);
            state.intermittentFaultActive = faultOccurs(rng);
            break;
        }
        case DegradationScenario::SEVERE:
            increment = 0.00085;
            break;
    }

    state.level = std::clamp(state.level + increment + variation(rng), 0.0, 1.0);
}

double Simulation::performanceSeverity(const Station& station) const {
    const DegradationState& state = degradation[station.id];
    const double onset = std::clamp((state.level - 0.10) / 0.70, 0.0, 1.0);
    return onset * onset * (3.0 - 2.0 * onset); // smooth onset, not linear wear
}

Simulation::PhysicalActivity Simulation::physicalActivity(const Station& station) const {
    if (station.state == StationState::PROCESSING) return PhysicalActivity::PROCESSING_LOAD;
    if (station.state == StationState::BLOCKED && station.currentUnit.has_value()) {
        return PhysicalActivity::HOLDING_UNIT;
    }
    return PhysicalActivity::STANDBY;
}

void Simulation::emitObservableData(const Station& station, UnitId unitId) {
    const bool defective = latentDefects[unitId];
    if (station.archetype == "MANUAL") {
        std::bernoulli_distribution observedFail(defective ? 0.35 : 0.015);
        output.writeManualCheck(currentTime, station.id, unitId, "VISUAL_ALIGNMENT",
            observedFail(rng) ? "FAIL" : "PASS");
    }

    if (station.archetype == "INSPECTION") {
        std::bernoulli_distribution detection(defective ? 0.70 : 0.0);
        const bool found = detection(rng);
        output.writeInspectionResult(currentTime, station.id, unitId,
            found ? "WELD_MISALIGNMENT" : "", found ? std::optional<int>(4) : std::nullopt,
            found ? "FAIL" : "PASS");
    }
}

Time Simulation::sensorSamplingInterval(const Station& station) const {
    return station.sensorCoverage == "HIGH" ? 10'000 : 30'000;
}

Time Simulation::nextSensorSamplingInterval(const Station& station) {
    const Time nominal = sensorSamplingInterval(station);
    const Time jitterBound = station.sensorCoverage == "HIGH" ? 1'200 : 3'500;
    std::uniform_int_distribution<Time> jitter(-jitterBound, jitterBound);
    return std::max<Time>(1'000, nominal + jitter(sensorRng));
}

void Simulation::emitSensorSample(const Station& station) {
    const DegradationState& degradationState = degradation[station.id];
    const double severity = performanceSeverity(station);
    const double sensorDrift = 0.25 * degradationState.level + 0.75 * severity;
    const PhysicalActivity activity = physicalActivity(station);
    const bool processing = activity == PhysicalActivity::PROCESSING_LOAD;
    const bool defectiveUnit = processing && station.currentUnit.has_value() &&
        latentDefects[*station.currentUnit];

    const double activityVibration = processing ? 1.15 :
        (activity == PhysicalActivity::HOLDING_UNIT ? 0.48 : 0.18);
    const double activityTemperature = processing ? 61.0 :
        (activity == PhysicalActivity::HOLDING_UNIT ? 45.0 : 39.0);
    const double activityCurrent = processing ? 14.5 :
        (activity == PhysicalActivity::HOLDING_UNIT ? 4.5 : 2.2);
    const double intermittentEffect = degradationState.intermittentFaultActive ? 0.35 : 0.0;

    SensorState& sensor = sensorStates[station.id];
    auto respond = [this](double current, double target, double responseRate,
                          double noiseStd) {
        std::normal_distribution<double> noise(0.0, noiseStd);
        return std::max(0.0, current + responseRate * (target - current) + noise(sensorRng));
    };

    sensor.vibration = respond(sensor.vibration,
        activityVibration + sensorDrift * 1.6 + intermittentEffect +
            (defectiveUnit ? 0.18 : 0.0),
        0.65, 0.035);
    sensor.temperature = respond(sensor.temperature,
        activityTemperature + sensorDrift * 13.0,
        0.12, 0.16);
    sensor.current = respond(sensor.current,
        activityCurrent + sensorDrift * 1.3 + intermittentEffect,
        0.85, 0.12);

    output.writeSensorReading(currentTime, station.id, "VIBRATION",
        sensor.vibration, "g");
    output.writeSensorReading(currentTime, station.id, "TEMPERATURE",
        sensor.temperature, "C");

    if (station.sensorCoverage == "HIGH") {
        output.writeSensorReading(currentTime, station.id, "CURRENT",
            sensor.current, "A");
        if (processing) {
            sensor.torque = respond(sensor.torque,
                42.0 - sensorDrift * 3.5 + (defectiveUnit ? -1.5 : 0.0),
                0.75, 0.22);
            output.writeSensorReading(currentTime, station.id, "TORQUE",
                sensor.torque, "Nm");
        }
    }
}

void Simulation::handleSensorSample(const SimEvent& event) {
    const Station& station = stations[event.stationId];
    emitSensorSample(station);
    scheduleEvent({
        currentTime + nextSensorSamplingInterval(station),
        SimEventType::SENSOR_SAMPLE,
        station.id,
        0
    });
}

void Simulation::tryStartProcessing(Station& station) {
    if (station.state == StationState::PROCESSING ||
        station.state == StationState::BLOCKED) {
        return;
    }

    UnitId unitId;

    if (station.isSource) unitId = createUnit();
    else {
        if (station.buffer.empty()) {
            station.state = StationState::STARVED;
            return;
        }

        unitId = station.buffer.front();
        station.buffer.pop();
        tryUnblockUpstream(station);
    }

    StationState previousState = station.state;

    station.currentUnit = unitId;
    station.currentCycleTime = sampleCycleTime(station);
    station.state = StationState::PROCESSING;

    output.writeStationEvent({
        currentTime,
        StationEventType::PROCESSING_STARTED,
        station.id,
        unitId,
        station.buffer.size(),
        previousState,
        StationState::PROCESSING,
        station.currentCycleTime
    });

    scheduleCheckpointEvents(station, unitId);

    scheduleEvent({
        currentTime + station.currentCycleTime,
        SimEventType::PROCESSING_COMPLETE,
        station.id,
        unitId
    });
}

void Simulation::scheduleCheckpointEvents(const Station& station, UnitId unitId) {
    if (station.archetype != "MANUAL") return;

    std::uniform_int_distribution<Time> jitter(-5'000, 5'000);
    const Time completionTime = currentTime + station.currentCycleTime;
    for (std::size_t index = 0; index < checkpoints.size(); ++index) {
        const CheckpointDefinition& checkpoint = checkpoints[index];
        if (checkpoint.stationId != station.id) continue;

        const Time nominal = currentTime + static_cast<Time>(std::round(
            checkpoint.nominalProgressFraction * station.currentCycleTime));
        const Time timestamp = std::clamp(nominal + jitter(checkpointRng), currentTime + 1,
            completionTime - 1);

        std::bernoulli_distribution readOccurs(checkpoint.readReliability);
        if (readOccurs(checkpointRng)) {
            checkpointEvents.push({timestamp, SimEventType::CHECKPOINT_RECORDED,
                station.id, unitId, index});
        }

        std::bernoulli_distribution falsePositive(checkpoint.falsePositiveRate);
        if (unitId > 1 && falsePositive(checkpointRng)) {
            const UnitId adjacentUnit = station.buffer.empty() ? unitId - 1 :
                station.buffer.front();
            checkpointEvents.push({timestamp, SimEventType::CHECKPOINT_RECORDED,
                station.id, adjacentUnit, index});
        }
    }
}

void Simulation::flushCheckpointEvents(Time until) {
    while (!checkpointEvents.empty()) {
        const SimEvent event = checkpointEvents.top();
        checkpointEvents.pop();
        if (event.timestamp > until || !event.checkpointIndex.has_value()) continue;

        const CheckpointDefinition& checkpoint = checkpoints[*event.checkpointIndex];
        const std::string eventType = checkpoint.checkpointType == "RFID" ?
            "RFID_CHECKPOINT" : "POWER_DRAW";
        output.writeCheckpointEvent(event.timestamp, eventType, event.stationId,
            event.unitId, checkpoint.checkpointId);
    }
}

void Simulation::handleProcessingComplete(const SimEvent& event) {
    Station& station = stations[event.stationId];
    advanceDegradation(station);
    if (station.id >= 3 && station.id <= 15 && !latentDefects[event.unitId]) {
        const double severity = performanceSeverity(station);
        const bool intermittent = degradation[station.id].intermittentFaultActive;
        std::bernoulli_distribution introduced(0.002 + severity * 0.020 +
            (intermittent ? 0.008 : 0.0));
        latentDefects[event.unitId] = introduced(rng);
    }

    output.writeStationEvent({
        currentTime,
        StationEventType::PROCESSING_COMPLETED,
        station.id,
        event.unitId,
        station.buffer.size(),
        StationState::PROCESSING,
        std::nullopt,
        station.currentCycleTime
    });

    emitObservableData(station, event.unitId);

    if (station.isSink) {
        station.currentUnit.reset();
        station.currentCycleTime = 0;
        station.state = StationState::IDLE;
        tryStartProcessing(station);
        return;
    }

    Station& nextStation = stations[station.id + 1];

    if (canAcceptUnit(nextStation)) {
        station.currentUnit.reset();
        station.currentCycleTime = 0;
        nextStation.buffer.push(event.unitId);

        output.writeStationEvent({
            currentTime,
            StationEventType::UNIT_ARRIVED,
            nextStation.id,
            event.unitId,
            nextStation.buffer.size(),
            std::nullopt,
            std::nullopt,
            std::nullopt
        });

        station.state = StationState::IDLE;
        tryStartProcessing(nextStation);
        tryStartProcessing(station);
    }
    else {
        station.state = StationState::BLOCKED;
    }
}
