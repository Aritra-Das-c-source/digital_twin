#pragma once

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <vector>

#include "Station.hpp"
#include "StationEvent.hpp"

class OutputWriter
{
   public:
    explicit OutputWriter(const std::filesystem::path& outputDirectory);

    void writeStations(const std::vector<Station>& stations);

    void writeStationCheckpoints(const std::vector<CheckpointDefinition>& checkpoints);

    void writeUnit(UnitId unitId, Time createdAt);

    void writeStationEvent(const StationEvent& event);

    void writeCheckpointEvent(Time timestamp, const std::string& eventType, StationId stationId,
                              std::optional<UnitId> unitId, const std::string& checkpointId);

    void writeSensorReading(Time timestamp, StationId stationId, const std::string& sensorType,
                            double value, const std::string& unit);

    void writeManualCheck(Time timestamp, StationId stationId, std::optional<UnitId> unitId,
                          const std::string& checkType, const std::string& result);

    void writeInspectionResult(Time timestamp, StationId stationId, UnitId unitId,
                               const std::string& defectType, std::optional<int> severity,
                               const std::string& result);

    void writeRunMetadata(const std::string& runId, std::uint32_t randomSeed, Time duration,
                          std::size_t stationCount, UnitId unitsCreated);

   private:
    std::filesystem::path outputDirectory;

    std::ofstream unitsFile;
    std::ofstream stationEventsFile;
    std::ofstream sensorReadingsFile;
    std::ofstream manualChecksFile;
    std::ofstream inspectionResultsFile;
    std::ofstream checkpointEventsFile;

    std::uint64_t nextEventId = 1;
    std::uint64_t nextCheckpointEventId = 1;
};
