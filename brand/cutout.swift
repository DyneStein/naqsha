import Foundation
import Vision
import CoreImage
import CoreImage.CIFilterBuiltins
import ImageIO
import UniformTypeIdentifiers

// Removes the background with Apple's Vision subject-lifting model and writes a transparent PNG.
let a = CommandLine.arguments
guard a.count == 3 else { fputs("usage: cutout <in> <out.png>\n", stderr); exit(2) }
let input = CIImage(contentsOf: URL(fileURLWithPath: a[1]))!
let req = VNGenerateForegroundInstanceMaskRequest()
let handler = VNImageRequestHandler(ciImage: input)
try handler.perform([req])
guard let obs = req.results?.first else { fputs("no subject found\n", stderr); exit(1) }
let mask = try obs.generateScaledMaskForImage(forInstances: obs.allInstances, from: handler)
let blend = CIFilter.blendWithMask()
blend.inputImage = input
blend.maskImage = CIImage(cvPixelBuffer: mask)
blend.backgroundImage = CIImage.empty()
let ctx = CIContext()
let cg = ctx.createCGImage(blend.outputImage!, from: input.extent)!
let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: a[2]) as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(dest, cg, nil)
CGImageDestinationFinalize(dest)
print("ok", a[2])
