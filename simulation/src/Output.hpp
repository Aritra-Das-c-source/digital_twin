#pragma once

#include "Station.hpp"
#include "StationEvent.hpp"

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <vector>

class OutputWriter {
public:
    explicit OutputWriter(
        const std::filesystem::path& outputDirectory
    );

    void writeStations(
        const std::vector<Station>& stations
    );

    void writeUnit(
        UnitId unitId,
        Time createdAt
    );

    void writeStationEvent(
        const StationEvent& event
    );

private:
    std::filesystem::path outputDirectory;

    std::ofstream unitsFile;
    std::ofstream stationEventsFile;

    std::uint64_t nextEventId = 1;
};