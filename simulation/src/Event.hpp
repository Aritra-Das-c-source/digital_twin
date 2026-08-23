#pragma once

#include "Station.hpp"

enum class SimEventType {
    PROCESSING_COMPLETE,
    SENSOR_SAMPLE
};

struct SimEvent {
    Time timestamp;
    SimEventType type;
    StationId stationId;
    UnitId unitId;
};
