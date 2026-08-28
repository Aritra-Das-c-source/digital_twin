#include "RuntimeArchive.hpp"

#include <chrono>
#include <cstdlib>
#include <stdexcept>
#include <vector>

namespace
{
std::string powerShellQuote(const std::filesystem::path& value)
{
    std::string text = value.string();
    std::string quoted = "'";
    for (const char character : text)
        quoted += character == '\'' ? "''" : std::string(1, character);
    return quoted + "'";
}

void runPowerShell(const std::string& script, const std::vector<std::filesystem::path>& arguments,
                   const std::string& failureMessage)
{
    std::string command = "powershell.exe -NoProfile -NonInteractive -Command \"& { " + script +
                          " }\"";
    for (const auto& argument : arguments)
        command += " " + powerShellQuote(argument);
    if (std::system(command.c_str()) != 0)
        throw std::runtime_error(failureMessage);
}

std::filesystem::path uniqueWorkingDirectory()
{
    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path() /
           ("digital_twin_simulation_" + std::to_string(stamp));
}

std::vector<std::filesystem::path> filesNamed(const std::filesystem::path& directory,
                                               const std::string& filename)
{
    std::vector<std::filesystem::path> matches;
    for (const auto& entry : std::filesystem::recursive_directory_iterator(directory))
        if (entry.is_regular_file() && entry.path().filename() == filename)
            matches.push_back(entry.path());
    return matches;
}
}  // namespace

RuntimeArchive::RuntimeArchive(const std::filesystem::path& configZip)
{
    if (!std::filesystem::is_regular_file(configZip))
        throw std::runtime_error("Configuration ZIP does not exist: " + configZip.string());
    if (configZip.extension() != ".zip")
        throw std::runtime_error("Configuration input must be a .zip file");

    workingDirectory = uniqueWorkingDirectory();
    extractedConfigDirectory = workingDirectory / "config";
    csvDirectory = workingDirectory / "csv";
    std::filesystem::create_directories(extractedConfigDirectory);
    std::filesystem::create_directories(csvDirectory);
    runPowerShell("param($zip, $destination) Expand-Archive -LiteralPath $zip -DestinationPath $destination -Force",
                  {std::filesystem::absolute(configZip), extractedConfigDirectory},
                  "Failed to extract configuration ZIP");
}

RuntimeArchive::~RuntimeArchive()
{
    std::error_code error;
    std::filesystem::remove_all(workingDirectory, error);
}

const std::filesystem::path& RuntimeArchive::configDirectory() const
{
    return extractedConfigDirectory;
}

const std::filesystem::path& RuntimeArchive::outputDirectory() const
{
    return csvDirectory;
}

void RuntimeArchive::createOutputZip(const std::filesystem::path& outputZip) const
{
    if (outputZip.extension() != ".zip")
        throw std::runtime_error("Output path must end in .zip");
    const auto absoluteOutput = std::filesystem::absolute(outputZip);
    if (absoluteOutput.has_parent_path())
        std::filesystem::create_directories(absoluteOutput.parent_path());
    runPowerShell("param($source, $destination) Compress-Archive -LiteralPath (Get-ChildItem -LiteralPath $source -Filter *.csv | Select-Object -ExpandProperty FullName) -DestinationPath $destination -Force",
                  {csvDirectory, absoluteOutput}, "Failed to create output ZIP");
}

std::filesystem::path findConfigurationFile(const std::filesystem::path& directory,
                                            const std::string& filename)
{
    const auto matches = filesNamed(directory, filename);
    if (matches.size() != 1)
        throw std::runtime_error("Configuration ZIP must contain exactly one " + filename);
    return matches.front();
}

std::filesystem::path findScenarioFile(const std::filesystem::path& directory)
{
    const auto direct = filesNamed(directory, "scenario.json");
    if (direct.size() == 1)
        return direct.front();
    if (direct.size() > 1)
        throw std::runtime_error("Configuration ZIP contains more than one scenario.json");

    std::vector<std::filesystem::path> scenarios;
    const auto scenarioDirectory = directory / "scenarios";
    if (std::filesystem::is_directory(scenarioDirectory))
        for (const auto& entry : std::filesystem::directory_iterator(scenarioDirectory))
            if (entry.is_regular_file() && entry.path().extension() == ".json")
                scenarios.push_back(entry.path());
    if (scenarios.size() != 1)
        throw std::runtime_error("Configuration ZIP must contain scenario.json or exactly one JSON file in scenarios/");
    return scenarios.front();
}

std::string archiveStem(const std::filesystem::path& archive)
{
    return archive.stem().string();
}
