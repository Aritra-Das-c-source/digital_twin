#include <filesystem>
#include <iostream>

#include "ConfigLoader.hpp"
#include "RuntimeArchive.hpp"
#include "Simulation.hpp"

int main(int argc, char** argv)
{
    try
    {
        std::filesystem::path factory = "config/factory.json";
        std::filesystem::path scenario = "config/scenarios/mixed_dark_zone.json";
        std::filesystem::path defects = "config/defects.json";
        std::filesystem::path output = "output/run_001";
        std::filesystem::path configZip;

        for (int i = 1; i < argc; ++i)
        {
            const std::string argument = argv[i];
            if (argument == "--factory" && i + 1 < argc)
            {
                factory = argv[++i];
            }
            else if (argument == "--scenario" && i + 1 < argc)
            {
                scenario = argv[++i];
            }
            else if (argument == "--defects" && i + 1 < argc)
            {
                defects = argv[++i];
            }
            else if (argument == "--output" && i + 1 < argc)
            {
                output = argv[++i];
            }
            else if (argument == "--config" && i + 1 < argc)
            {
                configZip = argv[++i];
            }
            else if (argument == "--help")
            {
                std::cout << "simulation --config CONFIG.zip --output OUTPUT.zip\\n"
                             "Legacy mode: simulation [--factory FILE] [--scenario FILE] "
                             "[--defects FILE] [--output DIR]\\n";
                return 0;
            }
            else
            {
                throw std::runtime_error("unknown or incomplete argument: " + argument);
            }
        }

        if (!configZip.empty())
        {
            if (argc == 1 || output == "output/run_001")
                throw std::runtime_error("--output OUTPUT.zip is required with --config");
            RuntimeArchive archive(configZip);
            factory = findConfigurationFile(archive.configDirectory(), "factory.json");
            defects = findConfigurationFile(archive.configDirectory(), "defects.json");
            scenario = findScenarioFile(archive.configDirectory());
            const std::string runName = archiveStem(configZip);
            Simulation simulation(ConfigLoader::load(factory, scenario, defects), archive.outputDirectory(),
                                  runName);
            simulation.run();
            archive.createOutputZip(output);
        }
        else
        {
            const std::string runName = scenario.stem().string();
            Simulation simulation(ConfigLoader::load(factory, scenario, defects), output, runName);
            simulation.run();
        }
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << '\\n';
        return 1;
    }
}
