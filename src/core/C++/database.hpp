#pragma once

#include "models.hpp"

#include <optional>
#include <string>
#include <unordered_map>

struct ItemOverride {
    std::string startTc;
    std::string endTc;
};

class Database {
public:
    virtual ~Database() = default;

    virtual std::optional<DocumentState> getDocumentBySignature(const std::string& fileSignature) = 0;
    virtual std::unordered_map<std::string, ItemOverride> getOverrides(const std::string& fileSignature) = 0;
    virtual void saveDocument(const DocumentState& document) = 0;
    virtual void addRecentFile(const std::string& filePath, const std::string& fileName) = 0;
};
