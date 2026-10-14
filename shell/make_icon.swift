// Draws the app icon: a navy tile, a pale line rising across it, and a glowing lamp-coloured
// dot at the latest point (the same "end dot" the charts inside the app use).
//   swiftc make_icon.swift -o make_icon && ./make_icon out.png
import Cocoa

let size = 1024
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4,
                           hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let g = NSGraphicsContext.current!.cgContext
let space = CGColorSpaceCreateDeviceRGB()
func rgb(_ hex: Int, _ a: CGFloat = 1) -> CGColor {
    CGColor(colorSpace: space, components: [CGFloat((hex >> 16) & 255) / 255, CGFloat((hex >> 8) & 255) / 255, CGFloat(hex & 255) / 255, a])!
}

// Apple's icon shape: an 824-point rounded square centred on a 1024 canvas.
let tile = CGRect(x: 100, y: 100, width: 824, height: 824)
g.addPath(CGPath(roundedRect: tile, cornerWidth: 186, cornerHeight: 186, transform: nil))
g.clip()
let sky = CGGradient(colorsSpace: space, colors: [rgb(0x1B2740), rgb(0x0A0F1A)] as CFArray, locations: [0, 1])!
g.drawLinearGradient(sky, start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 100), options: [])

let points = [CGPoint(x: 232, y: 372), CGPoint(x: 378, y: 492), CGPoint(x: 498, y: 414), CGPoint(x: 636, y: 566), CGPoint(x: 772, y: 672)]
let lamp = points.last!
let glow = CGGradient(colorsSpace: space, colors: [rgb(0xF2B24C, 0.55), rgb(0xF2B24C, 0.16), rgb(0xF2B24C, 0)] as CFArray, locations: [0, 0.45, 1])!
g.drawRadialGradient(glow, startCenter: lamp, startRadius: 0, endCenter: lamp, endRadius: 430, options: [])

g.setLineWidth(38); g.setLineCap(.round); g.setLineJoin(.round)
g.setStrokeColor(rgb(0xECE8DD))
g.addLines(between: points)
g.strokePath()

g.setFillColor(rgb(0x0E1420)); g.fillEllipse(in: CGRect(x: lamp.x - 74, y: lamp.y - 74, width: 148, height: 148))   // ring in the surface colour
g.setFillColor(rgb(0xF2B24C)); g.fillEllipse(in: CGRect(x: lamp.x - 52, y: lamp.y - 52, width: 104, height: 104))

NSGraphicsContext.current = nil
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
