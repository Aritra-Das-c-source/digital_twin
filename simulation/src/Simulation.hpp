#pragma once

#include "Event.hpp"
#include "Station.hpp"

#include <queue>
#include <vector>

struct EventCompare {
    bool operator()(const Event& a, const Event& b) const {
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
    std::vector<Station> stations;
    std::priority_queue<Event, std::vector<Event>, EventCompare> events;

    void initializeFactory();
    void scheduleEvent(const Event& event);
    void handleEvent(const Event& event);
    void handleProcessingComplete(const Event& event);
    void tryStartProcessing(Station& station);
    UnitId createUnit();
};