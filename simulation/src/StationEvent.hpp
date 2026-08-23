#pragma once

#include "Station.hpp"
#include <optional>

enum class StationEventType {
    UNIT_ARRIVED,
    PROCESSING_STARTED,
    PROCESSING_COMPLETED,
    STATE_CHANGED
};

struct StationEvent {
    Time timestamp;
    StationEventType type;
    StationId stationId;
    std::optional<UnitId> unitId;
    std::optional<std::size_t> queueLengthAfter;
    std::optional<StationState> previousState;
    std::optional<StationState> newState;
    std::optional<Time> cycleTime;
};