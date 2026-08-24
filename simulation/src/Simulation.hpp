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
    enum class DegradationScenario {
        HEALTHY,
        GRADUAL,
        ACCELERATING,
        STEP,
        INTERMITTENT,
        SEVERE
    };

    enum class PhysicalActivity {
        STANDBY,
        PROCESSING_LOAD,
        HOLDING_UNIT
    };

    struct DegradationState {
        DegradationScenario scenario = DegradationScenario::HEALTHY;
        double level = 0.0;
        std::uint64_t completedCycles = 0;
        bool intermittentFaultActive = false;
    };

    struct SensorState {
        double temperature = 40.0;
        double vibration = 0.18;
        double current = 2.2;
        double torque = 42.0;
    };

    Time currentTime = 0;
    UnitId nextUnitId = 1;
    std::mt19937 rng;
    std::mt19937 sensorRng;
    std::mt19937 checkpointRng;
    std::unordered_map<UnitId, bool> latentDefects;
    std::vector<DegradationState> degradation;
    std::vector<SensorState> sensorStates;
    std::vector<Station> stations;
    std::vector<CheckpointDefinition> checkpoints;
    std::priority_queue<SimEvent, std::vector<SimEvent>, EventCompare> events;
    std::priority_queue<SimEvent, std::vector<SimEvent>, EventCompare> checkpointEvents;
    OutputWriter output;

    void initializeFactory();
    void initializeCheckpointConfig();
    Time sampleCycleTime(const Station& station);
    void advanceDegradation(const Station& station);
    double performanceSeverity(const Station& station) const;
    PhysicalActivity physicalActivity(const Station& station) const;
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
    Time nextSensorSamplingInterval(const Station& station);
    void scheduleCheckpointEvents(const Station& station, UnitId unitId);
    void flushCheckpointEvents(Time until);
};
