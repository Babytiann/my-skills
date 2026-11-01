// vision_ocr.swift: Apple Vision OCR over a video (or one image), JSON out.
//
// Usage:
//   xcrun swift vision_ocr.swift INPUT [--fps 4] [--start S] [--end S] [--max-height 1080]
//        [--level accurate|fast] [--lang zh-Hans,en-US] [--min-height 0.01] [--out FILE]
//
// Frames are decoded by AVFoundation (hardware, no temp files) at --fps; OCR runs
// on frames scaled to at most --max-height (subtitles at 540p are ~20 px tall,
// too small for reliable Chinese OCR, so read the 1080p source, not the proxy).
// Output: {"source", "width", "height", "duration_s", "fps_sampled", "samples":
// [{"t", "text", "box": [x, y, w, h], "conf"}]}. Boxes are normalised with a
// top-left origin (Vision's is bottom-left; it is flipped here), the format
// stats.link_text expects. Text colour and stroke are measured later from pixels.

import AVFoundation
import Foundation
import ImageIO
import Vision

struct Options {
    var input = ""
    var fps = 4.0
    var start = 0.0
    var end: Double? = nil
    var maxHeight = 1080.0
    var level = VNRequestTextRecognitionLevel.accurate
    var langs = ["zh-Hans", "en-US"]
    var minHeight = 0.0
    var out: String? = nil
}

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write(("vision_ocr: " + msg + "\n").data(using: .utf8)!)
    exit(2)
}

func note(_ msg: String) {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
}

// NSDecimalNumber so JSONSerialization prints 0.4157, not 0.41570000000000001.
func r(_ x: Double, _ digits: Int = 4) -> NSDecimalNumber {
    let p = pow(10.0, Double(digits))
    return NSDecimalNumber(string: String((x * p).rounded() / p))
}

func parse() -> Options {
    var o = Options()
    let a = Array(CommandLine.arguments.dropFirst())
    var i = 0
    func value() -> String {
        i += 1
        if i >= a.count { fail("missing value for \(a[i - 1])") }
        return a[i]
    }
    func number() -> Double {
        let s = value()
        guard let v = Double(s) else { fail("not a number: \(s)") }
        return v
    }
    while i < a.count {
        switch a[i] {
        case "--fps": o.fps = number()
        case "--start": o.start = number()
        case "--end": o.end = number()
        case "--max-height": o.maxHeight = number()
        case "--min-height": o.minHeight = number()
        case "--level":
            switch value() {
            case "accurate": o.level = .accurate
            case "fast": o.level = .fast
            default: fail("--level is accurate or fast")
            }
        case "--lang": o.langs = value().split(separator: ",").map(String.init)
        case "--out": o.out = value()
        case "-h", "--help":
            print("usage: xcrun swift vision_ocr.swift INPUT [--fps 4] [--start S] [--end S] "
                + "[--max-height 1080] [--level accurate|fast] [--lang zh-Hans,en-US] "
                + "[--min-height F] [--out FILE]")
            exit(0)
        default:
            if a[i].hasPrefix("--") || !o.input.isEmpty { fail("unexpected argument \(a[i])") }
            o.input = a[i]
        }
        i += 1
    }
    if o.input.isEmpty { fail("give a video or image file") }
    if o.fps <= 0 { fail("--fps must be > 0") }
    return o
}

func recognise(_ image: CGImage, _ o: Options) throws -> [[String: Any]] {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = o.level
    req.recognitionLanguages = o.langs
    req.usesLanguageCorrection = true
    if o.minHeight > 0 { req.minimumTextHeight = Float(o.minHeight) }
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([req])
    var hits: [[String: Any]] = []
    for obs in req.results ?? [] {
        guard let c = obs.topCandidates(1).first else { continue }
        let text = c.string.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { continue }
        let b = obs.boundingBox
        hits.append(["text": text, "conf": r(Double(c.confidence), 3),
                     "box": [r(b.minX), r(1 - b.maxY), r(b.width), r(b.height)]])
    }
    return hits
}

let o = parse()
let url = URL(fileURLWithPath: (o.input as NSString).expandingTildeInPath)
if !FileManager.default.fileExists(atPath: url.path) { fail("no such file: \(o.input)") }

var result: [String: Any] = [
    "source": url.lastPathComponent, "fps_sampled": o.fps, "langs": o.langs,
    "level": o.level == .accurate ? "accurate" : "fast",
]
var samples: [[String: Any]] = []

if ["png", "jpg", "jpeg", "tif", "tiff", "heic", "bmp"].contains(url.pathExtension.lowercased()) {
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { fail("cannot read image \(o.input)") }
    result["width"] = img.width
    result["height"] = img.height
    result["duration_s"] = 0.0
    for var hit in try recognise(img, o) {
        hit["t"] = 0.0
        samples.append(hit)
    }
} else {
    let asset = AVURLAsset(url: url)
    let duration = try await asset.load(.duration).seconds
    guard let track = try await asset.loadTracks(withMediaType: .video).first else { fail("no video track") }
    let natural = try await track.load(.naturalSize)
    let transform = try await track.load(.preferredTransform)
    let size = natural.applying(transform)
    let w = abs(size.width), h = abs(size.height)
    let gen = AVAssetImageGenerator(asset: asset)
    gen.appliesPreferredTrackTransform = true
    if h > o.maxHeight { gen.maximumSize = CGSize(width: (w * o.maxHeight / h).rounded(), height: o.maxHeight) }
    let step = 1.0 / o.fps
    let tol = CMTime(seconds: step / 4, preferredTimescale: 60000)
    gen.requestedTimeToleranceBefore = tol
    gen.requestedTimeToleranceAfter = tol
    result["width"] = Int(w)
    result["height"] = Int(h)
    result["duration_s"] = r(duration, 3)
    let end = min(o.end ?? duration, duration)
    let total = max(0, Int(((end - o.start) * o.fps).rounded(.up)))
    var failed = 0
    for n in 0..<total {
        let t = o.start + Double(n) * step
        do {
            let (img, actual) = try await gen.image(at: CMTime(seconds: t, preferredTimescale: 60000))
            for var hit in try recognise(img, o) {
                hit["t"] = r(actual.seconds, 3)
                samples.append(hit)
            }
        } catch {
            failed += 1
            if failed <= 5 { note("frame at \(String(format: "%.2f", t)) s failed: \(error.localizedDescription)") }
        }
        if (n + 1) % 400 == 0 { note("ocr \(n + 1)/\(total) frames") }
    }
    result["frames_sampled"] = total
    result["frames_failed"] = failed
}

result["samples"] = samples
let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys, .withoutEscapingSlashes])
if let out = o.out {
    try data.write(to: URL(fileURLWithPath: (out as NSString).expandingTildeInPath))
    note("vision_ocr: \(samples.count) text hits -> \(out)")
} else {
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}
