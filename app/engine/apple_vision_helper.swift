// Worker Vision a lunga vita: legge da stdin una riga JSON per richiesta
// ({"path":"...","lang":"it-IT"}) e scrive su stdout una riga JSON con il risultato
// ({"type":"result","text":"..."}) o un errore ({"type":"error","message":"..."}).
// La prima riga di stdout è {"type":"ready"} dopo l'inizializzazione.

import Foundation
import Vision
import CoreGraphics
import ImageIO

let stdout = FileHandle.standardOutput
let stderr = FileHandle.standardError

func emit(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj, options: []) else {
        return
    }
    stdout.write(data)
    stdout.write("\n".data(using: .utf8)!)
}

func loadCGImage(path: String) -> CGImage? {
    let url = URL(fileURLWithPath: path)
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(source, 0, nil)
}

// Circonda l'immagine con un bordo bianco per dare a Vision più "respiro"
// sui margini. Senza padding, l'engine talvolta tronca le ultime parole
// di una riga quando il testo arriva vicino al bordo della pagina.
func padCGImage(_ image: CGImage, pad: Int) -> CGImage? {
    if pad <= 0 { return image }
    let newWidth = image.width + pad * 2
    let newHeight = image.height + pad * 2
    let colorSpace = CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(
        data: nil,
        width: newWidth,
        height: newHeight,
        bitsPerComponent: 8,
        bytesPerRow: newWidth * 4,
        space: colorSpace,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else { return nil }
    ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: newWidth, height: newHeight))
    ctx.draw(
        image,
        in: CGRect(x: pad, y: pad, width: image.width, height: image.height)
    )
    return ctx.makeImage()
}

func recognize(path: String, lang: String, useLanguageCorrection: Bool,
               padPixels: Int) -> [String: Any] {
    guard let cgImage = loadCGImage(path: path) else {
        return ["type": "error", "message": "impossibile aprire l'immagine: \(path)"]
    }
    let imageForVision = padCGImage(cgImage, pad: padPixels) ?? cgImage
    let request = VNRecognizeTextRequest()
    // Forza l'ultima revisione disponibile (su Tahoe è la più accurata,
    // altrimenti il sistema sceglierebbe un default più vecchio).
    if let maxRev = VNRecognizeTextRequest.supportedRevisions.max() {
        request.revision = maxRev
    }
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = useLanguageCorrection
    if !lang.isEmpty {
        request.recognitionLanguages = [lang]
    }
    let handler = VNImageRequestHandler(cgImage: imageForVision, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return ["type": "error", "message": "errore Vision: \(error.localizedDescription)"]
    }
    let observations = request.results ?? []
    var lines: [String] = []
    for obs in observations {
        if let top = obs.topCandidates(1).first {
            lines.append(top.string)
        }
    }
    return ["type": "result", "text": lines.joined(separator: "\n")]
}

// Segnala che siamo pronti a ricevere richieste.
emit(["type": "ready"])

while let line = readLine(strippingNewline: true) {
    let trimmed = line.trimmingCharacters(in: .whitespaces)
    if trimmed.isEmpty { continue }
    guard let data = trimmed.data(using: .utf8),
          let req = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let path = req["path"] as? String else {
        emit(["type": "error", "message": "richiesta non valida"])
        continue
    }
    let lang = (req["lang"] as? String) ?? ""
    // Default true per retro-compatibilità: se la chiave manca, comportamento storico.
    let useLanguageCorrection = (req["useLanguageCorrection"] as? Bool) ?? true
    // Padding bianco intorno all'immagine prima dell'OCR. Default 40 px.
    let padPixels = (req["padPixels"] as? Int) ?? 40
    emit(recognize(
        path: path,
        lang: lang,
        useLanguageCorrection: useLanguageCorrection,
        padPixels: padPixels
    ))
}
