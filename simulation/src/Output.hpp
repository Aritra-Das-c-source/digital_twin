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

    void writeSensorReading(
        Time timestamp, StationId stationId, const std::string& sensorType,
        double value, const std::string& unit
    );

    void writeManualCheck(
        Time timestamp, StationId stationId, UnitId unitId,
        const std::string& checkType, const std::string& result
    );

    void writeInspectionResult(
        Time timestamp, StationId stationId, UnitId unitId,
        const std::string& defectType, std::optional<int> severity,
        const std::string& result
    );

    void writeRunMetadata(
        const std::string& runId, std::uint32_t randomSeed,
        Time duration, std::size_t stationCount, UnitId unitsCreated
    );

private:
    std::filesystem::path outputDirectory;

    std::ofstream unitsFile;
    std::ofstream stationEventsFile;
    std::ofstream sensorReadingsFile;
    std::ofstream manualChecksFile;
    std::ofstream inspectionResultsFile;

    std::uint64_t nextEventId = 1;
};
