#pragma once

#include <string>
#include <unordered_map>
#include <vector>

struct ScriptItem {
    std::string id;
    int rowIndex = 0;
    std::string rawTimestamp;
    std::string startTimecode;
    double startSeconds = 0.0;
    std::string endTimecode;
    double endSeconds = 0.0;
    double durationSeconds = 0.0;
    std::string durationDisplay;
    std::string text;
    std::string meta;
    bool isSingle = false;
    int syncId = 0;
    std::vector<std::string> links;
    bool isChecked = false;
};

struct EditorNote {
    std::string id;
    int blockId = 0;
    std::string text;
};

struct Heading {
    int id = 0;
    std::string text;
    int level = 1;
};

struct DocumentState {
    std::string id;
    std::string fileSignature;
    std::string name;
    std::string filePath;
    std::string rawHtml;
    std::vector<ScriptItem> items;
    std::vector<EditorNote> notes;

    // The original uploaded parser reads this from your real models/database layer.
    // Replace the type if your actual custom note model is different.
    std::vector<std::string> customNotes;

    std::vector<Heading> headings;
    std::vector<std::string> links;
    std::unordered_map<std::string, bool> checkedLinks;
    std::unordered_map<std::string, std::string> linkColors;
};
