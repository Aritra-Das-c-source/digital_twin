#include "Simulation.hpp"

#include <iostream>

Simulation::Simulation() {
    initializeFactory();
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
        Event event = events.top();
        events.pop();

        if (event.timestamp > until) break;

        currentTime = event.timestamp;
        handleEvent(event);
    }
}

void Simulation::scheduleEvent(const Event& event) {
    events.push(event);
}

void Simulation::handleEvent(const Event& event) {
    switch (event.type) {
        case EventType::PROCESSING_COMPLETE:
            handleProcessingComplete(event);
            break;
    }
}

UnitId Simulation::createUnit() {
    return nextUnitId++;
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

    station.currentUnit = unitId;
    station.state = StationState::PROCESSING;

    std::cout
        << "[t=" << currentTime << "] "
        << "Station " << station.id
        << " starts Unit " << unitId
        << '\n';

    scheduleEvent({
        currentTime + station.cycleTime,
        EventType::PROCESSING_COMPLETE,
        station.id,
        unitId
    });
}

void Simulation::handleProcessingComplete(const Event& event) {
    Station& station = stations[event.stationId];

    std::cout
        << "[t=" << currentTime << "] "
        << "Station " << station.id
        << " completes Unit " << event.unitId
        << '\n';

    station.currentUnit.reset();

    if (station.isSink) {
        station.state = StationState::IDLE;
        return;
    }

    Station& nextStation = stations[station.id + 1];
    nextStation.buffer.push(event.unitId);

    std::cout
        << "[t=" << currentTime << "] "
        << "Unit " << event.unitId
        << " moves to Station " << nextStation.id
        << '\n';

    tryStartProcessing(nextStation);
    station.state = StationState::IDLE;
    tryStartProcessing(station);
}