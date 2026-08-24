#pragma once

#include "Config.hpp"

#include <filesystem>

class ConfigLoader {
public:
    static SimulationConfig load(const std::filesystem::path& factoryFile,
                                 const std::filesystem::path& scenarioFile,
                                 const std::filesystem::path& defectsFile);
};
