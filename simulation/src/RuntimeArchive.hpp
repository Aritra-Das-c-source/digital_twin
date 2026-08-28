#pragma once

#include <filesystem>
#include <string>

class RuntimeArchive
{
   public:
    explicit RuntimeArchive(const std::filesystem::path& configZip);
    ~RuntimeArchive();

    RuntimeArchive(const RuntimeArchive&) = delete;
    RuntimeArchive& operator=(const RuntimeArchive&) = delete;

    [[nodiscard]] const std::filesystem::path& configDirectory() const;
    [[nodiscard]] const std::filesystem::path& outputDirectory() const;
    void createOutputZip(const std::filesystem::path& outputZip) const;

   private:
    std::filesystem::path workingDirectory;
    std::filesystem::path extractedConfigDirectory;
    std::filesystem::path csvDirectory;
};

std::filesystem::path findConfigurationFile(const std::filesystem::path& directory,
                                            const std::string& filename);
std::filesystem::path findScenarioFile(const std::filesystem::path& directory);
std::string archiveStem(const std::filesystem::path& archive);
