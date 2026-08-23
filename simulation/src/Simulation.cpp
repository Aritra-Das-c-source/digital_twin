#include "Simulation.hpp"

#include <iostream>

Simulation::Simulation() : output("output/run_001")
{
    initializeFactory();
    output.writeStations(stations);
}

void Simulation::initializeFactory() {
    stations = {
        {0, 10, true,  false},
        {1, 20, false, false},
        {2, 15, false, false},
        {3, 0,  false, true}
    };
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

    station.currentUnit.reset();

    if (station.isSink) {
        station.state = StationState::IDLE;
        return;
    }

    Station& nextStation = stations[station.id + 1];
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

    tryStartProcessing(nextStation);
    station.state = StationState::IDLE;
    tryStartProcessing(station);
}