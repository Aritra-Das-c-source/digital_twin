#include "ObservationPolicy.hpp"

ObservationPolicy::ObservationPolicy(const std::vector<DarkZoneConfig>& zones) : zones(zones) {}

const DarkZoneConfig* ObservationPolicy::zoneFor(StationId stationId) const {
    for (const DarkZoneConfig& zone : zones) if (stationId >= zone.startStationId && stationId <= zone.endStationId) return &zone;
    return nullptr;
}
bool ObservationPolicy::shouldExportStationEvent(StationId id) const { return zoneFor(id) == nullptr; }
bool ObservationPolicy::shouldExportSensorTelemetry(StationId id) const { const auto* zone = zoneFor(id); return !zone || zone->observability.sensorTelemetry; }
bool ObservationPolicy::shouldExportManualCheck(StationId id) const { const auto* zone = zoneFor(id); return !zone || zone->observability.manualChecks; }
bool ObservationPolicy::shouldExposeCheckpointIdentity(StationId id, bool identifiesUnit) const { const auto* zone = zoneFor(id); return identifiesUnit && (!zone || zone->observability.checkpoints); }
bool ObservationPolicy::shouldExportCheckpoint(StationId id) const { const auto* zone = zoneFor(id); return !zone || zone->observability.checkpoints; }
const DarkZoneConfig* ObservationPolicy::enteredZone(StationId from, StationId to) const { const auto* zone = zoneFor(to); return zone && from + 1 == zone->startStationId ? zone : nullptr; }
const DarkZoneConfig* ObservationPolicy::exitedZone(StationId from, StationId to) const { const auto* zone = zoneFor(from); return zone && to == zone->endStationId + 1 ? zone : nullptr; }
