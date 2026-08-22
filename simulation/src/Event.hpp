#pragma once

#include "Station.hpp"

enum class EventType {
    PROCESSING_COMPLETE
};

struct Event {
    Time timestamp;
    EventType type;
    StationId stationId;
    UnitId unitId;
};