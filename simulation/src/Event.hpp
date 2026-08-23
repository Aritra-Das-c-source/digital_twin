#pragma once

#include "Station.hpp"

enum class SimEventType {
    PROCESSING_COMPLETE
};

struct SimEvent {
    Time timestamp;
    SimEventType type;
    StationId stationId;
    UnitId unitId;
};