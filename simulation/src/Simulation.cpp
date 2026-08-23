#include "Simulation.hpp"

#include <iostream>

Simulation::Simulation() : output("output/run_001")
{
    initializeFactory();
    output.writeStations(stations);
}

void Simulation::initializeFactory() {
    stations = {
        {0, 10, true, false, StationState::IDLE, 3},
        {1, 20, false, false, StationState::IDLE, 3},
        {2, 15, false, false, StationState::IDLE, 2},
        {3, 0, false, true, StationState::IDLE, 1}
    };
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

    while (!events.empty()) {
        SimEvent event = events.top();
        events.pop();

        if (event.timestamp > until) break;

        currentTime = event.timestamp;
        handleEvent(event);
    }
}

void Simulation::scheduleEvent(const SimEvent& event) {
    events.push(event);
}

void Simulation::handleEvent(const SimEvent& event) {
    switch (event.type) {
        case SimEventType::PROCESSING_COMPLETE:
            handleProcessingComplete(event);
            break;
    }
}

UnitId Simulation::createUnit() {
    UnitId id = nextUnitId++;
    output.writeUnit(id, currentTime);
    return id;
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
    station.state = StationState::PROCESSING;

    output.writeStationEvent({
        currentTime,
        StationEventType::PROCESSING_STARTED,
        station.id,
        unitId,
        station.buffer.size(),
        previousState,
        StationState::PROCESSING,
        station.cycleTime
    });

    scheduleEvent({
        currentTime + station.cycleTime,
        SimEventType::PROCESSING_COMPLETE,
        station.id,
        unitId
    });
}

void Simulation::handleProcessingComplete(const SimEvent& event) {
    Station& station = stations[event.stationId];

    output.writeStationEvent({
        currentTime,
        StationEventType::PROCESSING_COMPLETED,
        station.id,
        event.unitId,
        station.buffer.size(),
        StationState::PROCESSING,
        std::nullopt,
        station.cycleTime
    });

    if (station.isSink) {
        station.currentUnit.reset();
        station.state = StationState::IDLE;
        tryStartProcessing(station);
        return;
    }

    Station& nextStation = stations[station.id + 1];

    if (canAcceptUnit(nextStation)) {
        station.currentUnit.reset();
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