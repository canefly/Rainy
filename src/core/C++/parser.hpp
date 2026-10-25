#pragma once

#include "database.hpp"
#include "models.hpp"

#include <QtCore/QObject>
#include <QtCore/QRunnable>
#include <QtCore/QString>

#include <functional>
#include <string>
#include <tuple>
#include <unordered_map>
#include <vector>

struct RawTimeResult {
    std::string timecode;
    int totalSeconds = 0;
};

struct SingleTimeResult {
    std::string timecode;
    double totalSeconds = 0.0;
    bool hasTypo = false;
};

struct TimeSpanResult {
    std::string startTimecode;
    double startSeconds = 0.0;
    std::string endTimecode;
    double endSeconds = 0.0;
    double durationSeconds = 0.0;
    std::string durationDisplay;
    bool hasWarning = false;
    std::string warningReason;
};

using ProgressCallback = std::function<void(int, const std::string&)>;

std::string computeFileHash(const std::string& filePath);
std::string normalizeLink(const std::string& link);
RawTimeResult processTimeRaw(const std::string& raw);
SingleTimeResult parseTimeSingle(const std::string& raw);
TimeSpanResult processTimeSpan(const std::string& raw);
std::unordered_map<std::string, std::string> generateLinkColors(const std::vector<std::string>& links);
std::string renderSpanText(const std::string& text);

DocumentState parseDocxFile(
    const std::string& filePath,
    Database& db,
    ProgressCallback progressCallback = {}
);

class ParseWorkerSignals : public QObject {
    Q_OBJECT

public:
    using QObject::QObject;

signals:
    void progress(int percent, QString message);
    void finished(QString fileSignature);
    void error(QString message);
};

class DocxParseWorker : public QRunnable {
public:
    DocxParseWorker(std::string filePath, Database& db);

    void run() override;

    ParseWorkerSignals* signals() const;

private:
    std::string filePath_;
    Database& db_;
    ParseWorkerSignals* signals_;
};
