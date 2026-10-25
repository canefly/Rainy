#include "parser.hpp"

#include <QtCore/QByteArray>
#include <QtGui/QColor>
#include <QtCore/QCryptographicHash>
#include <QtCore/QDir>
#include <QtXml/QDomDocument>
#include <QtCore/QFile>
#include <QtCore/QFileInfo>
#include <QtCore/QTemporaryDir>
#include <QtCore/QUuid>

#include <quazip/quazip.h>
#include <quazip/quazipfile.h>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <iomanip>
#include <optional>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <unordered_set>

namespace {

struct SpanData {
    std::string text;
    std::string color;
    bool bold = false;
    bool italic = false;
    std::optional<double> sizePt;
    std::optional<std::string> link;
};

struct LineData {
    std::string text;
    std::vector<SpanData> spans;
    std::vector<std::string> hiddenLinks;
    std::vector<std::string> extractedImages;
    int origIndex = 0;
    bool isEditorLine = false;
    bool isHeader = false;
    std::optional<int> headerLevel;
};

struct BlockData {
    int id = 0;
    std::string text;
    std::vector<SpanData> spans;
    std::vector<std::string> images;
    bool isEditorLine = false;
    bool isHeader = false;
    std::optional<int> headerLevel;
};

struct Relationship {
    std::string id;
    std::string type;
    std::string target;
};

struct FontStyle {
    std::optional<std::string> color;
    std::optional<bool> bold;
    std::optional<bool> italic;
    std::optional<double> sizePt;
};

struct WordStyle {
    std::string styleId;
    std::string name;
    std::string basedOn;
    FontStyle font;
};

struct TimestampMatch {
    std::string start;
    std::string end;
    bool isSingle = false;
};

std::string trim(const std::string& value) {
    const std::string whitespace = " \t\n\r\f\v";
    const auto start = value.find_first_not_of(whitespace);

    if (start == std::string::npos) {
        return "";
    }

    const auto end = value.find_last_not_of(whitespace);
    return value.substr(start, end - start + 1);
}

std::string toLower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return value;
}

bool containsIgnoreCase(const std::string& text, const std::string& needle) {
    return toLower(text).find(toLower(needle)) != std::string::npos;
}

bool startsWithIgnoreCase(const std::string& text, const std::string& prefix) {
    if (text.size() < prefix.size()) {
        return false;
    }

    return toLower(text.substr(0, prefix.size())) == toLower(prefix);
}

bool endsWith(const std::string& text, const std::string& suffix) {
    if (text.size() < suffix.size()) {
        return false;
    }

    return text.compare(text.size() - suffix.size(), suffix.size(), suffix) == 0;
}

std::vector<std::string> split(const std::string& text, char delimiter) {
    std::vector<std::string> parts;
    std::stringstream ss(text);
    std::string part;

    while (std::getline(ss, part, delimiter)) {
        parts.push_back(part);
    }

    return parts;
}

std::vector<std::string> splitRegex(const std::string& text, const std::regex& pattern) {
    std::sregex_token_iterator begin(text.begin(), text.end(), pattern, -1);
    std::sregex_token_iterator end;
    return {begin, end};
}

std::string pad2(const std::string& value) {
    if (value.size() >= 2) {
        return value;
    }

    return std::string(2 - value.size(), '0') + value;
}

std::string replaceAll(std::string text, const std::string& from, const std::string& to) {
    if (from.empty()) {
        return text;
    }

    std::size_t start = 0;
    while ((start = text.find(from, start)) != std::string::npos) {
        text.replace(start, from.length(), to);
        start += to.length();
    }

    return text;
}

std::string htmlEscape(const std::string& text) {
    return QString::fromStdString(text).toHtmlEscaped().toStdString();
}

std::string makeUuid() {
    return QUuid::createUuid().toString(QUuid::WithoutBraces).toStdString();
}

std::string formatNumber(double value) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(2) << value;
    return out.str();
}

QByteArray readZipEntry(QuaZip& zip, const QString& entryName) {
    if (!zip.setCurrentFile(entryName)) {
        return {};
    }

    QuaZipFile file(&zip);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }

    return file.readAll();
}

std::unordered_map<std::string, Relationship> parseRelationships(const QByteArray& xmlBytes) {
    std::unordered_map<std::string, Relationship> relationships;

    if (xmlBytes.isEmpty()) {
        return relationships;
    }

    QDomDocument doc;
    QString errorMessage;
    int errorLine = 0;
    int errorColumn = 0;

    if (!doc.setContent(xmlBytes, &errorMessage, &errorLine, &errorColumn)) {
        throw std::runtime_error(
            "Could not parse document relationships XML at line " +
            std::to_string(errorLine) + ": " + errorMessage.toStdString()
        );
    }

    QDomNodeList nodes = doc.elementsByTagName("Relationship");

    for (int i = 0; i < nodes.count(); ++i) {
        QDomElement element = nodes.at(i).toElement();

        Relationship relationship;
        relationship.id = element.attribute("Id").toStdString();
        relationship.type = element.attribute("Type").toStdString();
        relationship.target = element.attribute("Target").toStdString();

        if (!relationship.id.empty()) {
            relationships[relationship.id] = relationship;
        }
    }

    return relationships;
}

std::optional<bool> parseOnOffElement(const QDomElement& parent, const QString& tagName) {
    QDomNodeList nodes = parent.elementsByTagName(tagName);

    if (nodes.isEmpty()) {
        return std::nullopt;
    }

    QDomElement element = nodes.at(0).toElement();
    QString val = element.attribute("w:val").trimmed().toLower();

    if (val.isEmpty()) {
        return true;
    }

    return !(val == "0" || val == "false" || val == "off" || val == "no");
}

FontStyle parseRunProperties(const QDomElement& runProperties) {
    FontStyle style;

    if (runProperties.isNull()) {
        return style;
    }

    QDomNodeList colorNodes = runProperties.elementsByTagName("w:color");
    if (!colorNodes.isEmpty()) {
        std::string val = colorNodes.at(0).toElement().attribute("w:val").toStdString();
        if (!val.empty() && toLower(val) != "auto") {
            style.color = "#" + val;
        }
    }

    style.bold = parseOnOffElement(runProperties, "w:b");
    style.italic = parseOnOffElement(runProperties, "w:i");

    QDomNodeList sizeNodes = runProperties.elementsByTagName("w:sz");
    if (!sizeNodes.isEmpty()) {
        bool ok = false;
        double halfPoints = sizeNodes.at(0).toElement().attribute("w:val").toDouble(&ok);
        if (ok) {
            style.sizePt = halfPoints / 2.0;
        }
    }

    return style;
}

std::unordered_map<std::string, WordStyle> parseStyles(const QByteArray& xmlBytes) {
    std::unordered_map<std::string, WordStyle> styles;

    if (xmlBytes.isEmpty()) {
        return styles;
    }

    QDomDocument doc;
    if (!doc.setContent(xmlBytes)) {
        return styles;
    }

    QDomNodeList styleNodes = doc.elementsByTagName("w:style");

    for (int i = 0; i < styleNodes.count(); ++i) {
        QDomElement styleElement = styleNodes.at(i).toElement();

        WordStyle style;
        style.styleId = styleElement.attribute("w:styleId").toStdString();

        QDomNodeList nameNodes = styleElement.elementsByTagName("w:name");
        if (!nameNodes.isEmpty()) {
            style.name = nameNodes.at(0).toElement().attribute("w:val").toStdString();
        }

        QDomNodeList basedOnNodes = styleElement.elementsByTagName("w:basedOn");
        if (!basedOnNodes.isEmpty()) {
            style.basedOn = basedOnNodes.at(0).toElement().attribute("w:val").toStdString();
        }

        QDomNodeList rPrNodes = styleElement.elementsByTagName("w:rPr");
        if (!rPrNodes.isEmpty()) {
            style.font = parseRunProperties(rPrNodes.at(0).toElement());
        }

        if (!style.styleId.empty()) {
            styles[style.styleId] = style;
        }
    }

    return styles;
}

void applyFontStyle(FontStyle& destination, const FontStyle& source) {
    if (source.color.has_value()) {
        destination.color = source.color;
    }
    if (source.bold.has_value()) {
        destination.bold = source.bold;
    }
    if (source.italic.has_value()) {
        destination.italic = source.italic;
    }
    if (source.sizePt.has_value()) {
        destination.sizePt = source.sizePt;
    }
}

FontStyle resolveParagraphStyle(
    const std::string& styleId,
    const std::unordered_map<std::string, WordStyle>& styles
) {
    FontStyle result;
    std::vector<const WordStyle*> chain;
    std::unordered_set<std::string> seen;
    std::string current = styleId;

    while (!current.empty() && !seen.count(current)) {
        seen.insert(current);

        auto it = styles.find(current);
        if (it == styles.end()) {
            break;
        }

        chain.push_back(&it->second);
        current = it->second.basedOn;
    }

    for (auto it = chain.rbegin(); it != chain.rend(); ++it) {
        applyFontStyle(result, (*it)->font);
    }

    return result;
}

std::string getParagraphStyleId(const QDomElement& paragraph) {
    QDomNode child = paragraph.firstChild();

    while (!child.isNull()) {
        if (child.isElement() && child.nodeName() == "w:pPr") {
            QDomNodeList styleNodes = child.toElement().elementsByTagName("w:pStyle");
            if (!styleNodes.isEmpty()) {
                return styleNodes.at(0).toElement().attribute("w:val").toStdString();
            }
        }

        child = child.nextSibling();
    }

    return "";
}

std::string getParagraphStyleName(
    const std::string& styleId,
    const std::unordered_map<std::string, WordStyle>& styles
) {
    auto it = styles.find(styleId);

    if (it != styles.end() && !it->second.name.empty()) {
        return it->second.name;
    }

    return styleId;
}

std::string readRunText(const QDomElement& run) {
    std::string text;
    QDomNode child = run.firstChild();

    while (!child.isNull()) {
        if (child.isElement()) {
            QString name = child.nodeName();

            if (name == "w:t") {
                text += child.toElement().text().toStdString();
            } else if (name == "w:tab") {
                text += "\t";
            } else if (name == "w:br" || name == "w:cr") {
                text += "\n";
            }
        }

        child = child.nextSibling();
    }

    return text;
}

FontStyle getRunStyle(
    const QDomElement& run,
    const FontStyle& paragraphStyle
) {
    FontStyle result = paragraphStyle;
    QDomNode child = run.firstChild();

    while (!child.isNull()) {
        if (child.isElement() && child.nodeName() == "w:rPr") {
            FontStyle runStyle = parseRunProperties(child.toElement());
            applyFontStyle(result, runStyle);
            break;
        }

        child = child.nextSibling();
    }

    return result;
}

std::vector<std::string> collectRelationshipIds(const QDomElement& element, const QString& attributeName) {
    std::vector<std::string> ids;

    std::function<void(const QDomNode&)> walk = [&](const QDomNode& node) {
        if (node.isElement()) {
            QDomElement e = node.toElement();
            if (e.hasAttribute(attributeName)) {
                ids.push_back(e.attribute(attributeName).toStdString());
            }
        }

        QDomNode child = node.firstChild();
        while (!child.isNull()) {
            walk(child);
            child = child.nextSibling();
        }
    };

    walk(element);
    return ids;
}

std::string paragraphText(const QDomElement& paragraph) {
    std::string text;

    std::function<void(const QDomNode&)> walk = [&](const QDomNode& node) {
        if (node.isElement()) {
            QString name = node.nodeName();
            if (name == "w:t") {
                text += node.toElement().text().toStdString();
                return;
            }
            if (name == "w:tab") {
                text += "\t";
                return;
            }
            if (name == "w:br" || name == "w:cr") {
                text += "\n";
                return;
            }
        }

        QDomNode child = node.firstChild();
        while (!child.isNull()) {
            walk(child);
            child = child.nextSibling();
        }
    };

    walk(paragraph);
    return trim(text);
}

std::string extractImage(
    QuaZip& zip,
    const Relationship& relationship,
    const std::string& tempImageDirectory
) {
    std::string target = relationship.target;

    while (startsWithIgnoreCase(target, "../")) {
        target = target.substr(3);
    }

    std::string zipPath = target;
    if (!startsWithIgnoreCase(zipPath, "word/")) {
        zipPath = "word/" + zipPath;
    }

    QByteArray bytes = readZipEntry(zip, QString::fromStdString(zipPath));
    if (bytes.isEmpty()) {
        return "";
    }

    std::filesystem::path targetPath(target);
    std::string extension = targetPath.extension().string();
    if (extension.empty()) {
        extension = ".png";
    }

    std::string fileName = makeUuid().substr(0, 8) + extension;
    std::filesystem::path outputPath = std::filesystem::path(tempImageDirectory) / fileName;

    QFile output(QString::fromStdString(outputPath.string()));
    if (!output.open(QIODevice::WriteOnly)) {
        return "";
    }

    output.write(bytes);
    output.close();

    return std::filesystem::absolute(outputPath).string();
}

std::vector<SpanData> parseParagraphSpans(
    const QDomElement& paragraph,
    const std::string& paragraphStyleId,
    const std::unordered_map<std::string, WordStyle>& styles,
    const std::unordered_map<std::string, Relationship>& relationships
) {
    std::vector<SpanData> spans;
    FontStyle paragraphStyle = resolveParagraphStyle(paragraphStyleId, styles);

    auto addRun = [&](const QDomElement& run, const std::optional<std::string>& link) {
        std::string text = readRunText(run);
        if (text.empty()) {
            return;
        }

        FontStyle font = getRunStyle(run, paragraphStyle);

        SpanData span;
        span.text = text;
        span.color = font.color.value_or("");
        span.bold = font.bold.value_or(false);
        span.italic = font.italic.value_or(false);
        span.sizePt = font.sizePt;
        span.link = link;

        spans.push_back(span);
    };

    QDomNode child = paragraph.firstChild();

    while (!child.isNull()) {
        if (!child.isElement()) {
            child = child.nextSibling();
            continue;
        }

        QDomElement element = child.toElement();

        if (child.nodeName() == "w:r") {
            addRun(element, std::nullopt);
        } else if (child.nodeName() == "w:hyperlink") {
            std::optional<std::string> link;
            std::string relationshipId = element.attribute("r:id").toStdString();

            auto relIt = relationships.find(relationshipId);
            if (relIt != relationships.end()) {
                link = relIt->second.target;
            }

            QDomNode hyperlinkChild = element.firstChild();
            while (!hyperlinkChild.isNull()) {
                if (hyperlinkChild.isElement() && hyperlinkChild.nodeName() == "w:r") {
                    addRun(hyperlinkChild.toElement(), link);
                }
                hyperlinkChild = hyperlinkChild.nextSibling();
            }
        }

        child = child.nextSibling();
    }

    std::vector<SpanData> merged;

    for (const SpanData& span : spans) {
        if (merged.empty()) {
            merged.push_back(span);
            continue;
        }

        SpanData& last = merged.back();
        bool sameStyle =
            last.color == span.color &&
            last.bold == span.bold &&
            last.italic == span.italic &&
            last.sizePt == span.sizePt &&
            last.link == span.link;

        if (sameStyle) {
            last.text += span.text;
        } else {
            merged.push_back(span);
        }
    }

    return merged;
}

bool allMeaningfulSpansBold(const std::vector<SpanData>& spans) {
    bool foundText = false;

    for (const SpanData& span : spans) {
        if (trim(span.text).empty()) {
            continue;
        }

        foundText = true;
        if (!span.bold) {
            return false;
        }
    }

    return foundText;
}

bool anyLargeFont(const std::vector<SpanData>& spans) {
    for (const SpanData& span : spans) {
        if (span.sizePt.has_value() && span.sizePt.value() >= 13.0) {
            return true;
        }
    }

    return false;
}

std::vector<TimestampMatch> findTimestampMatches(const std::string& text) {
    static const std::regex timestampRegex(
        R"((?:\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?\s*(?:[-–—]|to)\s*\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?)|(?:\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?))",
        std::regex::icase
    );

    std::vector<TimestampMatch> matches;

    for (std::sregex_iterator it(text.begin(), text.end(), timestampRegex), end; it != end; ++it) {
        const std::smatch& match = *it;

        TimestampMatch result;

        if (match[1].matched) {
            result.start = match[1].str();
            result.end = match[2].str();
            result.isSingle = false;
        } else if (match[3].matched) {
            result.start = match[3].str();
            result.isSingle = true;
        }

        if (!result.start.empty()) {
            matches.push_back(result);
        }
    }

    return matches;
}

std::string buildRawHtml(
    const std::vector<BlockData>& blocks,
    const std::unordered_map<std::string, std::string>& linkColors
) {
    std::string html;

    for (const BlockData& block : blocks) {
        int headerLevel = block.headerLevel.value_or(1);
        std::string innerContent;

        for (const SpanData& span : block.spans) {
            std::string escapedText = htmlEscape(span.text);
            std::string styledText = renderSpanText(escapedText);
            std::vector<std::string> styleProperties;

            if (!span.color.empty()) {
                styleProperties.push_back("color: " + span.color);
            }

            if (span.bold) {
                styleProperties.push_back("font-weight: bold");
            }

            if (span.italic) {
                styleProperties.push_back("font-style: italic");
            }

            if (span.sizePt.has_value()) {
                double emValue = std::round((span.sizePt.value() / 11.0) * 100.0) / 100.0;
                styleProperties.push_back("font-size: " + formatNumber(emValue) + "em");
            }

            std::string styleString;
            if (!styleProperties.empty()) {
                styleString = " style='";
                for (std::size_t i = 0; i < styleProperties.size(); ++i) {
                    if (i > 0) {
                        styleString += "; ";
                    }
                    styleString += styleProperties[i];
                }
                styleString += "'";
            }

            if (span.link.has_value()) {
                std::string normalized = normalizeLink(span.link.value());
                std::string color = "#38bdf8";

                auto colorIt = linkColors.find(normalized);
                if (colorIt != linkColors.end()) {
                    color = colorIt->second;
                }

                innerContent +=
                    "<a href='" + htmlEscape(span.link.value()) +
                    "' style='color: " + color +
                    "; text-decoration: underline; font-weight: bold;'>" +
                    styledText + "</a>";
            } else if (!styleProperties.empty()) {
                innerContent += "<span" + styleString + ">" + styledText + "</span>";
            } else {
                innerContent += styledText;
            }
        }

        std::string anchor = "<a name='block-" + std::to_string(block.id) + "'></a>";
        std::string id = "block-" + std::to_string(block.id);

        if (block.isHeader) {
            if (headerLevel == 1) {
                html += "<h1 id='" + id + "' style='margin: 18px 0 8px 0; font-size: 1.5em; font-weight: 800; color: #4ade80;'>" + anchor + innerContent + "</h1>";
            } else if (headerLevel == 2) {
                html += "<h2 id='" + id + "' style='margin: 14px 0 6px 0; font-size: 1.3em; font-weight: 700; color: #4ade80;'>" + anchor + innerContent + "</h2>";
            } else if (headerLevel == 3) {
                html += "<h3 id='" + id + "' style='margin: 12px 0 4px 0; font-size: 1.15em; font-weight: 700; color: #4ade80;'>" + anchor + innerContent + "</h3>";
            } else {
                html += "<h4 id='" + id + "' style='margin: 10px 0 4px 0; font-size: 1.05em; font-weight: 700; color: #4ade80;'>" + anchor + innerContent + "</h4>";
            }
        } else {
            html += "<p id='" + id + "' style='margin: 4px 0; line-height: 1.5;'>" + anchor + innerContent + "</p>";
        }
    }

    return html;
}

std::string getEpisodeMeta(const std::vector<LineData>& lines, int index, int distance) {
    static const std::regex fullRegex(
        R"(S(\d+)\s*E(\d+)|Season\s*(\d+)|Episode\s*(\d+))",
        std::regex::icase
    );

    static const std::regex seasonRegex(R"(S(\d+)|Season\s*(\d+))", std::regex::icase);
    static const std::regex episodeRegex(R"(E(\d+)|Episode\s*(\d+))", std::regex::icase);

    int start = std::max(0, index - distance);
    int end = std::min(static_cast<int>(lines.size()), index + distance);

    for (int i = start; i < end; ++i) {
        const std::string& text = lines[i].text;
        std::smatch fullMatch;

        if (!std::regex_search(text, fullMatch, fullRegex)) {
            continue;
        }

        std::string season;
        std::string episode;

        if (fullMatch[1].matched) {
            season = fullMatch[1].str();
        } else if (fullMatch[3].matched) {
            season = fullMatch[3].str();
        }

        if (fullMatch[2].matched) {
            episode = fullMatch[2].str();
        } else if (fullMatch[4].matched) {
            episode = fullMatch[4].str();
        }

        if (season.empty()) {
            std::smatch seasonMatch;
            if (std::regex_search(text, seasonMatch, seasonRegex)) {
                season = seasonMatch[1].matched ? seasonMatch[1].str() : seasonMatch[2].str();
            }
        }

        if (episode.empty()) {
            std::smatch episodeMatch;
            if (std::regex_search(text, episodeMatch, episodeRegex)) {
                episode = episodeMatch[1].matched ? episodeMatch[1].str() : episodeMatch[2].str();
            }
        }

        if (!season.empty() && !episode.empty()) {
            return "S" + pad2(season) + "E" + pad2(episode);
        }
    }

    return "Clip";
}

std::optional<std::string> findAssociatedLink(
    int pivotIndex,
    const std::vector<LineData>& lines
) {
    const int offsets[] = {0, -1, 1, 2, 3, 4};
    static const std::regex urlRegex(R"((https?://[^\s]+))", std::regex::icase);

    for (int offset : offsets) {
        int index = pivotIndex + offset;

        if (index < 0 || index >= static_cast<int>(lines.size())) {
            continue;
        }

        const LineData& line = lines[index];

        for (const SpanData& span : line.spans) {
            if (span.link.has_value() && !span.link->empty()) {
                return span.link;
            }
        }

        if (containsIgnoreCase(line.text, "http")) {
            std::smatch urlMatch;
            if (std::regex_search(line.text, urlMatch, urlRegex)) {
                return urlMatch[1].str();
            }
        }

        if (!line.hiddenLinks.empty()) {
            return line.hiddenLinks.front();
        }
    }

    return std::nullopt;
}

int findLineIndexByOrigIndex(const std::vector<LineData>& lines, int origIndex) {
    for (int i = 0; i < static_cast<int>(lines.size()); ++i) {
        if (lines[i].origIndex == origIndex) {
            return i;
        }
    }

    return -1;
}

void processTimestampMatch(
    const TimestampMatch& match,
    int pivotIndex,
    int syncId,
    const std::string& meta,
    const std::vector<LineData>& lines,
    std::vector<ScriptItem>& items,
    std::unordered_set<std::string>& linksSet,
    const std::optional<DocumentState>& existingDocument,
    const std::unordered_map<std::string, ItemOverride>& overrides
) {
    RawTimeResult start = processTimeRaw(match.start);
    RawTimeResult end;

    std::string rawTimestamp;
    bool isSingle = match.isSingle;

    if (isSingle) {
        rawTimestamp = match.start;
    } else {
        end = processTimeRaw(match.end);
        rawTimestamp = match.start + " - " + match.end;
    }

    double duration = 0.0;
    if (!isSingle) {
        duration = std::max(0, end.totalSeconds - start.totalSeconds);
    }

    int matchedLineIndex = findLineIndexByOrigIndex(lines, syncId);
    int realPivot = matchedLineIndex >= 0 ? matchedLineIndex : pivotIndex;

    std::optional<std::string> foundLink = findAssociatedLink(realPivot, lines);
    std::optional<std::string> normalizedLink;

    if (foundLink.has_value()) {
        std::string normalized = normalizeLink(foundLink.value());
        if (!normalized.empty()) {
            normalizedLink = normalized;
            linksSet.insert(normalized);
        }
    }

    int rowIndex = static_cast<int>(items.size()) + 1;
    std::string itemId = makeUuid();
    std::string startTimecode = start.timecode;
    std::string endTimecode = isSingle ? "" : end.timecode;

    auto overrideIt = overrides.find(itemId);
    if (overrideIt != overrides.end()) {
        if (!overrideIt->second.startTc.empty()) {
            startTimecode = overrideIt->second.startTc;
        }
        if (!overrideIt->second.endTc.empty()) {
            endTimecode = overrideIt->second.endTc;
        }
    }

    std::string durationDisplay = "--";

    if (duration >= 60.0) {
        int minutes = static_cast<int>(duration) / 60;
        int seconds = static_cast<int>(duration) % 60;
        durationDisplay = std::to_string(minutes) + "m";
        if (seconds > 0) {
            durationDisplay += " " + std::to_string(seconds) + "s";
        }
    } else if (duration > 0.0) {
        durationDisplay = std::to_string(static_cast<int>(duration)) + "s";
    }

    std::vector<std::string> itemLinks;
    if (normalizedLink.has_value()) {
        itemLinks.push_back(normalizedLink.value());
    }

    bool isChecked = false;
    if (existingDocument.has_value()) {
        for (const std::string& link : itemLinks) {
            auto checkedIt = existingDocument->checkedLinks.find(link);
            if (checkedIt != existingDocument->checkedLinks.end() && checkedIt->second) {
                isChecked = true;
                break;
            }
        }
    }

    ScriptItem item;
    item.id = itemId;
    item.rowIndex = rowIndex;
    item.rawTimestamp = rawTimestamp;
    item.startTimecode = startTimecode;
    item.startSeconds = static_cast<double>(start.totalSeconds);
    item.endTimecode = endTimecode;
    item.endSeconds = static_cast<double>(end.totalSeconds);
    item.durationSeconds = duration;
    item.durationDisplay = durationDisplay;
    item.text = lines[realPivot].text;
    item.meta = meta;
    item.isSingle = isSingle;
    item.syncId = syncId;
    item.links = itemLinks;
    item.isChecked = isChecked;

    items.push_back(item);
}

} // namespace

std::string computeFileHash(const std::string& filePath) {
    QFile file(QString::fromStdString(filePath));

    if (!file.open(QIODevice::ReadOnly)) {
        throw std::runtime_error("Could not open file for SHA-256: " + filePath);
    }

    QCryptographicHash hasher(QCryptographicHash::Sha256);

    while (!file.atEnd()) {
        QByteArray chunk = file.read(65536);
        hasher.addData(chunk);
    }

    return hasher.result().toHex().toStdString();
}

std::string normalizeLink(const std::string& link) {
    if (link.empty() || link == "#") {
        return link;
    }

    const std::vector<std::regex> youtubePatterns = {
        std::regex(R"(youtube\.com/watch\?[^\s#]*v=([a-zA-Z0-9_-]{11}))", std::regex::icase),
        std::regex(R"(youtube\.com/embed/([a-zA-Z0-9_-]{11}))", std::regex::icase),
        std::regex(R"(youtube\.com/v/([a-zA-Z0-9_-]{11}))", std::regex::icase),
        std::regex(R"(youtube\.com/shorts/([a-zA-Z0-9_-]{11}))", std::regex::icase),
        std::regex(R"(youtu\.be/([a-zA-Z0-9_-]{11}))", std::regex::icase)
    };

    for (const std::regex& pattern : youtubePatterns) {
        std::smatch match;
        if (std::regex_search(link, match, pattern)) {
            return "https://www.youtube.com/watch?v=" + match[1].str();
        }
    }

    std::string clean = std::regex_replace(link, std::regex(R"(#.*$)"), "");

    if (containsIgnoreCase(clean, "drive.google.com")) {
        clean = std::regex_replace(
            clean,
            std::regex(R"(/(?:view|edit|preview|copy|sharing).*$)", std::regex::icase),
            ""
        );

        if (clean.find("/file/d/") != std::string::npos) {
            clean = std::regex_replace(clean, std::regex(R"(\?.*$)"), "");
        }

        clean = std::regex_replace(clean, std::regex(R"([?&]\.\d+$)"), "");
    }

    clean = std::regex_replace(
        clean,
        std::regex(R"(([?&])(?:t|time|start|timestamp|at)=\d+&?)", std::regex::icase),
        "$1"
    );

    while (!clean.empty() && (clean.back() == '?' || clean.back() == '&')) {
        clean.pop_back();
    }

    return trim(clean);
}

RawTimeResult processTimeRaw(const std::string& raw) {
    if (raw.empty()) {
        return {"00:00:00:00", 0};
    }

    std::string cleaned = raw;
    cleaned = replaceAll(cleaned, "-", "");
    cleaned = replaceAll(cleaned, "–", "");
    cleaned = replaceAll(cleaned, "—", "");
    cleaned = replaceAll(cleaned, "(", "");
    cleaned = replaceAll(cleaned, ")", "");
    cleaned = trim(cleaned);

    std::vector<std::string> parts = split(cleaned, ':');

    std::string h;
    std::string m;
    std::string s;
    std::string f;

    if (parts.size() == 2) {
        m = parts[0];
        s = parts[1];
        h = "00";
        f = "00";
    } else if (parts.size() == 3) {
        h = parts[0];
        m = parts[1];
        s = parts[2];
        f = "00";
    } else if (parts.size() == 4) {
        h = parts[0];
        m = parts[1];
        s = parts[2];
        f = parts[3];
    } else {
        return {"00:00:00:00", 0};
    }

    h = pad2(h);
    m = pad2(m);
    s = pad2(s);
    f = pad2(f);

    try {
        int totalSeconds = std::stoi(h) * 3600 + std::stoi(m) * 60 + std::stoi(s);
        return {h + ":" + m + ":" + s + ":" + f, totalSeconds};
    } catch (...) {
        return {"00:00:00:00", 0};
    }
}

SingleTimeResult parseTimeSingle(const std::string& raw) {
    std::string cleaned = std::regex_replace(raw, std::regex(R"([()—–\-])"), "");
    cleaned = trim(cleaned);

    bool hasTypo = raw.find(';') != std::string::npos ||
                   raw.find('.') != std::string::npos ||
                   raw.find(' ') != std::string::npos;

    std::vector<std::string> rawParts = splitRegex(cleaned, std::regex(R"([:;.\s]+)"));
    std::vector<std::string> parts;

    for (const std::string& part : rawParts) {
        if (part.empty()) {
            continue;
        }

        if (std::all_of(part.begin(), part.end(), [](unsigned char c) { return std::isdigit(c); })) {
            parts.push_back(pad2(part));
        }
    }

    std::string h;
    std::string m;
    std::string s;
    std::string f;

    if (parts.size() == 1) {
        s = parts[0];
        h = "00";
        m = "00";
        f = "00";
    } else if (parts.size() == 2) {
        m = parts[0];
        s = parts[1];
        h = "00";
        f = "00";
    } else if (parts.size() == 3) {
        h = parts[0];
        m = parts[1];
        s = parts[2];
        f = "00";
    } else if (parts.size() >= 4) {
        h = parts[0];
        m = parts[1];
        s = parts[2];
        f = parts[3];
    } else {
        return {"00:00:00:00", 0.0, true};
    }

    try {
        double totalSeconds =
            std::stoi(h) * 3600.0 +
            std::stoi(m) * 60.0 +
            std::stoi(s) +
            std::stoi(f) / 30.0;

        return {h + ":" + m + ":" + s + ":" + f, totalSeconds, hasTypo};
    } catch (...) {
        return {"00:00:00:00", 0.0, true};
    }
}

TimeSpanResult processTimeSpan(const std::string& raw) {
    if (raw.empty()) {
        return {
            "00:00:00:00", 0.0,
            "00:00:00:00", 0.0,
            0.0, "--", false, ""
        };
    }

    std::vector<std::string> range = splitRegex(
        trim(raw),
        std::regex(R"(\s*(?:[-–—]|to)\s*)", std::regex::icase)
    );

    SingleTimeResult start = parseTimeSingle(range[0]);
    SingleTimeResult end = start;
    bool typoEnd = false;

    if (range.size() > 1 && !trim(range[1]).empty()) {
        end = parseTimeSingle(range[1]);
        typoEnd = end.hasTypo;
    }

    double duration = std::max(0.0, end.totalSeconds - start.totalSeconds);
    std::string durationDisplay = "--";

    if (duration >= 60.0) {
        int minutes = static_cast<int>(duration) / 60;
        int seconds = static_cast<int>(duration) % 60;

        durationDisplay = std::to_string(minutes) + "m";
        if (seconds > 0) {
            durationDisplay += " " + std::to_string(seconds) + "s";
        }
    } else if (duration > 0.0) {
        durationDisplay = std::to_string(static_cast<int>(duration)) + "s";
    }

    bool hasWarning = start.hasTypo || typoEnd;
    std::string warningReason = hasWarning ? "Normalized from '" + raw + "'" : "";

    return {
        start.timecode,
        start.totalSeconds,
        end.timecode,
        end.totalSeconds,
        duration,
        durationDisplay,
        hasWarning,
        warningReason
    };
}

std::unordered_map<std::string, std::string> generateLinkColors(const std::vector<std::string>& links) {
    std::unordered_map<std::string, std::string> colors;

    if (links.empty()) {
        return colors;
    }

    double count = static_cast<double>(links.size());

    for (std::size_t i = 0; i < links.size(); ++i) {
        double hue = std::fmod(static_cast<double>(i) * (360.0 / count), 360.0);
        QColor color = QColor::fromHslF(hue / 360.0, 0.75, 0.60);
        colors[links[i]] = color.name(QColor::HexRgb).toStdString();
    }

    return colors;
}

std::string renderSpanText(const std::string& text) {
    std::string result = std::regex_replace(
        text,
        std::regex(R"((@?editor\s*[:;]?))", std::regex::icase),
        "<span style='color: #facc15; font-weight: 700;'>$1</span>"
    );

    static const std::regex timeRegex(
        R"((?:\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?\s*(?:[-–—]|to)\s*\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?)|(?:\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?))",
        std::regex::icase
    );

    std::string output;
    std::size_t last = 0;

    for (std::sregex_iterator it(result.begin(), result.end(), timeRegex), end; it != end; ++it) {
        const std::smatch& match = *it;
        std::size_t position = static_cast<std::size_t>(match.position());
        std::size_t length = static_cast<std::size_t>(match.length());

        output.append(result, last, position - last);

        std::string raw = match.str();
        if (!trim(raw).empty() && raw.find(':') != std::string::npos) {
            output += "<a href='time:" + raw +
                      "' style='color: #38bdf8; text-decoration: underline; font-weight: 700; cursor: pointer;' data-time='" +
                      raw + "'>" + raw + "</a>";
        } else {
            output += raw;
        }

        last = position + length;
    }

    output.append(result, last, std::string::npos);
    return output;
}

DocumentState parseDocxFile(
    const std::string& filePath,
    Database& db,
    ProgressCallback progressCallback
) {
    const std::string fileSignature = computeFileHash(filePath);
    const std::string fileName = std::filesystem::path(filePath).filename().string();

    std::optional<DocumentState> existingDocument = db.getDocumentBySignature(fileSignature);
    std::unordered_map<std::string, ItemOverride> overrides = db.getOverrides(fileSignature);

    if (progressCallback) {
        progressCallback(10, "Opening document...");
    }

    QuaZip zip(QString::fromStdString(filePath));
    if (!zip.open(QuaZip::mdUnzip)) {
        throw std::runtime_error("Could not open DOCX as ZIP: " + filePath);
    }

    QByteArray documentXml = readZipEntry(zip, "word/document.xml");
    QByteArray relationshipsXml = readZipEntry(zip, "word/_rels/document.xml.rels");
    QByteArray stylesXml = readZipEntry(zip, "word/styles.xml");

    if (documentXml.isEmpty()) {
        throw std::runtime_error("DOCX has no word/document.xml");
    }

    std::unordered_map<std::string, Relationship> relationships = parseRelationships(relationshipsXml);
    std::unordered_map<std::string, WordStyle> styles = parseStyles(stylesXml);

    std::filesystem::path tempDirectory =
        std::filesystem::path(QDir::tempPath().toStdString()) /
        "knot_images" /
        fileSignature.substr(0, 12);

    std::filesystem::create_directories(tempDirectory);

    QDomDocument document;
    QString xmlError;
    int xmlErrorLine = 0;
    int xmlErrorColumn = 0;

    if (!document.setContent(documentXml, &xmlError, &xmlErrorLine, &xmlErrorColumn)) {
        throw std::runtime_error(
            "Could not parse word/document.xml at line " +
            std::to_string(xmlErrorLine) + ": " + xmlError.toStdString()
        );
    }

    QDomNodeList paragraphNodes = document.elementsByTagName("w:p");
    int totalParagraphs = paragraphNodes.count();
    std::vector<LineData> lines;

    for (int i = 0; i < totalParagraphs; ++i) {
        if (progressCallback && totalParagraphs > 0 && i % 15 == 0) {
            int percent = 15 + static_cast<int>((static_cast<double>(i) / totalParagraphs) * 50.0);
            progressCallback(
                percent,
                "Parsing paragraph " + std::to_string(i + 1) + " of " + std::to_string(totalParagraphs) + "..."
            );
        }

        QDomElement paragraph = paragraphNodes.at(i).toElement();
        std::string text = paragraphText(paragraph);

        std::vector<std::string> imageRelationshipIds = collectRelationshipIds(paragraph, "r:embed");
        bool hasImages = !imageRelationshipIds.empty();

        if (text.empty() && !hasImages) {
            continue;
        }

        std::string paragraphStyleId = getParagraphStyleId(paragraph);
        std::string styleName = getParagraphStyleName(paragraphStyleId, styles);

        bool isHeader = false;
        std::optional<int> headerLevel;

        if (!styleName.empty()) {
            if (startsWithIgnoreCase(styleName, "Heading")) {
                isHeader = true;
                std::smatch numberMatch;
                if (std::regex_search(styleName, numberMatch, std::regex(R"((\d+))"))) {
                    headerLevel = std::stoi(numberMatch[1].str());
                }
            } else if (containsIgnoreCase(styleName, "Title")) {
                isHeader = true;
                headerLevel = containsIgnoreCase(styleName, "Subtitle") ? 2 : 1;
            }
        }

        std::vector<SpanData> spans = parseParagraphSpans(
            paragraph,
            paragraphStyleId,
            styles,
            relationships
        );

        if (!isHeader && !text.empty() && !std::regex_search(text, std::regex(R"(@?editor)", std::regex::icase))) {
            std::string cleanText = trim(text);

            if (cleanText.size() <= 80 &&
                !endsWith(cleanText, ".") &&
                !endsWith(cleanText, "?") &&
                !endsWith(cleanText, "!") &&
                !endsWith(cleanText, ";")) {

                std::smatch sectionKeywordMatch;
                bool hasSectionKeyword = std::regex_search(
                    cleanText,
                    sectionKeywordMatch,
                    std::regex(
                        R"(^(lvl|level|chapter|section|part|tab|scene|act|outro|intro)\s*(\d*))",
                        std::regex::icase
                    )
                );

                bool allBold = allMeaningfulSpansBold(spans);
                bool largeFont = anyLargeFont(spans);

                if (hasSectionKeyword || allBold || largeFont) {
                    isHeader = true;

                    if (std::regex_search(cleanText, std::regex(R"(\b(lvl\s*2|level\s*2|sub[- ]?section)\b)", std::regex::icase))) {
                        headerLevel = 2;
                    } else if (std::regex_search(cleanText, std::regex(R"(\b(lvl\s*3|level\s*3)\b)", std::regex::icase))) {
                        headerLevel = 3;
                    } else if (hasSectionKeyword) {
                        std::string number = sectionKeywordMatch[2].str();
                        if (!number.empty()) {
                            int parsed = std::stoi(number);
                            headerLevel = (parsed >= 1 && parsed <= 4) ? parsed : 1;
                        } else {
                            headerLevel = 1;
                        }
                    } else if (largeFont) {
                        headerLevel = 1;
                    } else {
                        headerLevel = cleanText.size() < 40 ? 1 : 2;
                    }
                }
            }
        }

        std::vector<std::string> hiddenLinks;
        std::vector<std::string> relationshipIds = collectRelationshipIds(paragraph, "r:id");

        for (const std::string& relationshipId : relationshipIds) {
            auto relationshipIt = relationships.find(relationshipId);
            if (relationshipIt == relationships.end()) {
                continue;
            }

            if (containsIgnoreCase(relationshipIt->second.type, "hyperlink")) {
                hiddenLinks.push_back(relationshipIt->second.target);
            }
        }

        std::vector<std::string> extractedImages;
        for (const std::string& relationshipId : imageRelationshipIds) {
            auto relationshipIt = relationships.find(relationshipId);
            if (relationshipIt == relationships.end()) {
                continue;
            }

            if (!containsIgnoreCase(relationshipIt->second.type, "image")) {
                continue;
            }

            std::string imagePath = extractImage(
                zip,
                relationshipIt->second,
                tempDirectory.string()
            );

            if (!imagePath.empty()) {
                extractedImages.push_back(imagePath);
            }
        }

        LineData line;
        line.text = text;
        line.spans = spans;
        line.hiddenLinks = hiddenLinks;
        line.extractedImages = extractedImages;
        line.origIndex = i;
        line.isEditorLine = std::regex_search(text, std::regex(R"(@?editor)", std::regex::icase));
        line.isHeader = isHeader;
        line.headerLevel = headerLevel;

        lines.push_back(line);
    }

    std::vector<ScriptItem> items;
    std::unordered_set<std::string> linksSet;
    std::vector<EditorNote> notes;
    std::vector<std::string> warnings;
    std::unordered_set<int> processedLineIndices;

    const std::regex triggerRegex(
        R"(@?editor\s*[:;]?\s*.*(play|pay|lay|pla|ply|insert|inset|insrt|inst|show|use|using|clip|roll|cut|add))",
        std::regex::icase
    );

    for (int i = 0; i < static_cast<int>(lines.size()); ++i) {
        const LineData& line = lines[i];
        const std::string& textLine = line.text;

        if (line.isEditorLine) {
            if (std::regex_search(textLine, triggerRegex)) {
                bool foundTimestamp = false;
                std::string foundMeta = getEpisodeMeta(lines, i, 3);
                int searchLimit = std::min(i + 6, static_cast<int>(lines.size()));

                for (int j = i; j < searchLimit; ++j) {
                    const LineData& subLine = lines[j];

                    if (j > i && std::regex_search(subLine.text, triggerRegex)) {
                        break;
                    }

                    std::vector<TimestampMatch> matches = findTimestampMatches(subLine.text);

                    if (!matches.empty()) {
                        foundTimestamp = true;

                        for (const TimestampMatch& match : matches) {
                            processedLineIndices.insert(subLine.origIndex);
                            processTimestampMatch(
                                match,
                                i,
                                subLine.origIndex,
                                foundMeta,
                                lines,
                                items,
                                linksSet,
                                existingDocument,
                                overrides
                            );
                        }
                    }
                }

                if (!foundTimestamp) {
                    warnings.push_back("Found @editor tag but no timestamp. Moved to Editor's Notes.");
                    notes.push_back({makeUuid(), line.origIndex, textLine});
                }
            } else {
                std::string context = textLine.size() > 60 ? textLine.substr(0, 60) + "..." : textLine;
                warnings.push_back("Ignored Unknown Command: \"" + context + "\". Moved to Editor's Notes.");
                notes.push_back({makeUuid(), line.origIndex, textLine});
            }
        }

        if (!processedLineIndices.count(line.origIndex)) {
            std::vector<TimestampMatch> matches = findTimestampMatches(textLine);

            if (!matches.empty()) {
                bool hasLinkNearby = false;
                int start = std::max(0, i - 1);
                int end = std::min(i + 2, static_cast<int>(lines.size()));

                for (int k = start; k < end; ++k) {
                    if (containsIgnoreCase(lines[k].text, "http") || !lines[k].hiddenLinks.empty()) {
                        hasLinkNearby = true;
                        break;
                    }
                }

                if (hasLinkNearby) {
                    for (const TimestampMatch& match : matches) {
                        processedLineIndices.insert(line.origIndex);
                        std::string fallbackMeta = getEpisodeMeta(lines, i, 2);

                        processTimestampMatch(
                            match,
                            i,
                            line.origIndex,
                            fallbackMeta,
                            lines,
                            items,
                            linksSet,
                            existingDocument,
                            overrides
                        );
                    }
                }
            }
        }

        for (const std::string& hiddenLink : line.hiddenLinks) {
            std::string normalized = normalizeLink(hiddenLink);
            if (!normalized.empty()) {
                linksSet.insert(normalized);
            }
        }
    }

    std::vector<std::string> uniqueLinks(linksSet.begin(), linksSet.end());
    std::unordered_map<std::string, std::string> linkColors = generateLinkColors(uniqueLinks);

    std::vector<BlockData> blocks;
    std::vector<Heading> headings;

    for (const LineData& line : lines) {
        BlockData block;
        block.id = line.origIndex;
        block.text = line.text;
        block.spans = line.spans;
        block.images = line.extractedImages;
        block.isEditorLine = line.isEditorLine;
        block.isHeader = line.isHeader;
        block.headerLevel = line.headerLevel;

        blocks.push_back(block);

        if (line.isHeader) {
            Heading heading;
            heading.id = line.origIndex;
            heading.text = line.text;
            heading.level = line.headerLevel.value_or(1);
            headings.push_back(heading);
        }
    }

    std::string rawHtml = buildRawHtml(blocks, linkColors);

    if (progressCallback) {
        progressCallback(95, "Finishing up...");
    }

    DocumentState newDocument;
    newDocument.id = existingDocument.has_value() ? existingDocument->id : makeUuid();
    newDocument.fileSignature = fileSignature;
    newDocument.name = fileName;
    newDocument.filePath = filePath;
    newDocument.rawHtml = rawHtml;
    newDocument.items = items;
    newDocument.notes = notes;
    newDocument.customNotes = existingDocument.has_value() ? existingDocument->customNotes : std::vector<std::string>{};
    newDocument.headings = headings;
    newDocument.links = uniqueLinks;
    newDocument.checkedLinks = existingDocument.has_value() ? existingDocument->checkedLinks : std::unordered_map<std::string, bool>{};
    newDocument.linkColors = linkColors;

    db.saveDocument(newDocument);
    db.addRecentFile(filePath, fileName);

    if (progressCallback) {
        progressCallback(100, "Done!");
    }

    return newDocument;
}

DocxParseWorker::DocxParseWorker(std::string filePath, Database& db)
    : filePath_(std::move(filePath)),
      db_(db),
      signals_(new ParseWorkerSignals()) {
    setAutoDelete(true);
}

void DocxParseWorker::run() {
    try {
        DocumentState state = parseDocxFile(
            filePath_,
            db_,
            [this](int percent, const std::string& message) {
                emit signals_->progress(percent, QString::fromStdString(message));
            }
        );

        emit signals_->finished(QString::fromStdString(state.fileSignature));
    } catch (const std::exception& exception) {
        emit signals_->error(QString::fromStdString(exception.what()));
    }
}

ParseWorkerSignals* DocxParseWorker::signals() const {
    return signals_;
}
