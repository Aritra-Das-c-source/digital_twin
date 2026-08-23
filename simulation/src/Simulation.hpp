#pragma once

#include "Event.hpp"
#include "Station.hpp"
#include "Output.hpp"

#include <queue>
#include <vector>
#include <random>
#include <unordered_map>

struct EventCompare {
    bool operator()(const SimEvent& a, const SimEvent& b) const {
        return a.timestamp > b.timestamp;
    }
};

class Simulation {
public:
    Simulation();
    void run(Time until);

private:
    Time currentTime = 0;
    UnitId nextUnitId = 1;
    std::mt19937 rng;
    std::unordered_map<UnitId, bool> latentDefects;
    std::vector<double> stationWear;
    std::vector<Station> stations;
    std::priority_queue<SimEvent, std::vector<SimEvent>, EventCompare> events;
    OutputWriter output;

    void initializeFactory();
    Time sampleCycleTime(const Station& station);
    bool canAcceptUnit(const Station& station) const;
    void tryUnblockUpstream(Station& station);
    void scheduleEvent(const SimEvent& event);
    void handleEvent(const SimEvent& event);
    void handleProcessingComplete(const SimEvent& event);
    void handleSensorSample(const SimEvent& event);
    void tryStartProcessing(Station& station);
    UnitId createUnit();
    void emitObservableData(const Station& station, UnitId unitId);
    void emitSensorSample(const Station& station);
    Time sensorSamplingInterval(const Station& station) const;
};
