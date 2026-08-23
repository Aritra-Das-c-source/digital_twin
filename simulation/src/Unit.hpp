#pragma once

#include <cstdint>
#include <string>

using UnitId = std::uint64_t;

struct Unit {
    UnitId id;
    std::string vehicleModel;
    std::string supplierBatch;
};
