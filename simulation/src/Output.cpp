#include "Output.hpp"
#include <stdexcept>

OutputWriter::OutputWriter(const std::filesystem::path& outputDirectory)
    : outputDirectory(outputDirectory)
{
    std::filesystem::create_directories(outputDirectory);
    unitsFile.open(outputDirectory / "units.csv");
    stationEventsFile.open(outputDirectory / "station_events.csv");

    if (!unitsFile || !stationEventsFile) {
        throw std::runtime_error("Failed to open output files");
    }

    unitsFile
        << "unit_id,"
        << "created_at_ms,"
        << "vehicle_model,"
        << "supplier_batch\n";

    stationEventsFile
        << "event_id,"
        << "timestamp_ms,"
        << "event_type,"
        << "station_id,"
        << "unit_id,"
        << "queue_length_after,"
        << "previous_state,"
        << "new_state,"
        << "cycle_time_ms\n";
}

namespace {

std::string toString(StationState state) {
    switch (state) {
        case StationState::IDLE:
            return "IDLE";

        case StationState::PROCESSING:
            return "PROCESSING";

        case StationState::BLOCKED:
            return "BLOCKED";

        case StationState::STARVED:
            return "STARVED";
    }

    return "";
}

std::string toString(StationEventType type) {
    switch (type) {
        case StationEventType::UNIT_ARRIVED:
            return "UNIT_ARRIVED";

        case StationEventType::PROCESSING_STARTED:
            return "PROCESSING_STARTED";

        case StationEventType::PROCESSING_COMPLETED:
            return "PROCESSING_COMPLETED";

        case StationEventType::STATE_CHANGED:
            return "STATE_CHANGED";
    }

    return "";
}

}

void OutputWriter::writeStations(const std::vector<Station>& stations)
{
    std::ofstream file(outputDirectory / "stations.csv");

    if (!file) {
        throw std::runtime_error(
            "Failed to open stations.csv"
        );
    }

    file
        << "station_id,"
        << "name,"
        << "archetype,"
        << "base_cycle_time_ms,"
        << "cycle_time_std_ms,"
        << "buffer_capacity,"
        << "sensor_coverage\n";

    for (const Station& station : stations) {
        file
            << "S" << station.id << ','
            << "Station_" << station.id << ','
            << "AUTOMATED,"
            << station.currentCycleTime << ','
            << 0 << ','
            << station.bufferCapacity << ','
            << "NONE\n";
    }
}

void OutputWriter::writeUnit(UnitId unitId, Time createdAt)
{
    unitsFile
        << "U" << unitId << ','
        << createdAt << ','
        << "MODEL_A,"
        << "BATCH_001\n";
}

void OutputWriter::writeStationEvent(const StationEvent& event)
{
    stationEventsFile
        << "EV" << nextEventId++ << ','
        << event.timestamp << ','
        << toString(event.type) << ','
        << "S" << event.stationId << ',';

    if (event.unitId.has_value()) {
        stationEventsFile << "U" << *event.unitId;
    }

    stationEventsFile << ',';

    if (event.queueLengthAfter.has_value()) {
        stationEventsFile << *event.queueLengthAfter;
    }

    stationEventsFile << ',';

    if (event.previousState.has_value()) {
        stationEventsFile << toString(*event.previousState);
    }

    stationEventsFile << ',';

    if (event.newState.has_value()) {
        stationEventsFile << toString(*event.newState);
    }

    stationEventsFile << ',';

    if (event.cycleTime.has_value()) {
        stationEventsFile << *event.cycleTime;
    }

    stationEventsFile << '\n';
}