#pragma once

#include "Unit.hpp"

#include <cstdint>
#include <cstddef>
#include <optional>
#include <queue>

using StationId = std::uint32_t;
using Time = std::int64_t;

enum class StationState {
    IDLE,
    PROCESSING,
    BLOCKED,
    STARVED
};

struct Station {
    StationId id;
    Time meanCycleTime;
    double cycleTimeCV = 0.0;

    bool isSource = false;
    bool isSink = false;

    StationState state = StationState::IDLE;
    std::size_t bufferCapacity;
    std::queue<UnitId> buffer;
    std::optional<UnitId> currentUnit;
    Time currentCycleTime = 0;
};