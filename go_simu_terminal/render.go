package main

import (
	"bytes"
	_ "embed"
	"fmt"
	"html"
	"image/color"
	"os"
	"strings"

	"github.com/fogleman/gg"
	"github.com/golang/freetype/truetype"
	"golang.org/x/image/font"
	headlessterm "github.com/danielgatis/go-headless-term"
)

//go:embed Menlo.ttc
var embeddedASCIIFont []byte

//go:embed wqy-microhei.ttc
var embeddedCJKFont []byte

// loadFontFaceFromBytes loads a font face from bytes with hinting enabled.
func loadFontFaceFromBytes(fontBytes []byte, size float64) (font.Face, error) {
	if len(fontBytes) == 0 {
		return nil, fmt.Errorf("empty font data")
	}
	f, err := truetype.Parse(fontBytes)
	if err != nil {
		return nil, err
	}
	return truetype.NewFace(f, &truetype.Options{
		Size:    size,
		Hinting: font.HintingFull,
	}), nil
}

// loadFontFaceFromFile loads a font face from a file path.
func loadFontFaceFromFile(path string, size float64) (font.Face, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return loadFontFaceFromBytes(data, size)
}

// Cell/layout constants — aligned with Python version.
const (
	cellW = 9
	cellH = 20
	padX  = 4
	padY  = 4

	fontSize = 15

	defaultBGColor = "#121212"
	defaultFGColor = "#cccccc"
)

// ansiColors maps ANSI color indices 0–15 to hex strings.
var ansiColors = [16]string{
	"#000000", // 0  Black
	"#c50f1f", // 1  Red
	"#13a10e", // 2  Green
	"#c19c00", // 3  Yellow
	"#3a96dd", // 4  Blue
	"#881798", // 5  Magenta
	"#3a96dd", // 6  Cyan
	"#cccccc", // 7  LightGrey
	"#767676", // 8  DarkGrey
	"#e74856", // 9  LightRed
	"#16c60c", // 10 LightGreen
	"#f9f1a5", // 11 LightYellow
	"#3b78ff", // 12 LightBlue
	"#b4009e", // 13 LightMagenta
	"#61d6d6", // 14 LightCyan
	"#f2f2f2", // 15 White
}

// resolveColor converts a color.Color to a CSS hex string.
func resolveColor(c color.Color, isBG bool) string {
	if c == nil {
		if isBG {
			return defaultBGColor
		}
		return defaultFGColor
	}

	var idx int
	switch v := c.(type) {
	case *headlessterm.NamedColor:
		idx = int(v.Name)
		if idx == 256 { // Default FG
			return defaultFGColor
		}
		if idx == 257 { // Default BG
			return defaultBGColor
		}
		if idx < 16 {
			return ansiColors[idx]
		}
	case *headlessterm.IndexedColor:
		idx = int(v.Index)
		if idx < 16 {
			return ansiColors[idx]
		}
		if idx < 232 {
			idx -= 16
			b := idx % 6
			idx /= 6
			g := idx % 6
			r := idx / 6
			cv := func(x int) int {
				if x == 0 {
					return 0
				}
				return 55 + x*40
			}
			return fmt.Sprintf("#%02x%02x%02x", cv(r), cv(g), cv(b))
		}
		if idx < 256 {
			gray := 8 + (idx-232)*10
			return fmt.Sprintf("#%02x%02x%02x", gray, gray, gray)
		}
	}

	// Fallback for TrueColor or unknown colors
	r, g, b, _ := c.RGBA()
	return fmt.Sprintf("#%02x%02x%02x", r>>8, g>>8, b>>8)
}

// renderToSVG converts the current headless terminal state to an SVG string.
func renderToSVG(term *headlessterm.Terminal, cols, rows int) string {
	imgW := cols*cellW + padX*2
	imgH := rows*cellH + padY*2

	var sb strings.Builder

	// Add common CJK mono fonts to the CSS font-family for better fallback.
	fmt.Fprintf(&sb,
		`<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" `+
			`style="background:%s;font-family:'SF Mono','Menlo','Consolas','WenQuanYi Micro Hei Mono','Noto Sans Mono CJK SC','PingFang SC',monospace;font-size:%dpx">`,
		imgW, imgH, defaultBGColor, fontSize,
	)

	// Define a clip region per row to prevent wide glyphs from bleeding into adjacent cells.
	sb.WriteString("<defs>")
	for row := 0; row < rows; row++ {
		y := padY + row*cellH
		fmt.Fprintf(&sb, `<clipPath id="r%d"><rect x="%d" y="%d" width="%d" height="%d"/></clipPath>`,
			row, padX, y, cols*cellW, cellH)
	}
	sb.WriteString("</defs>")

	for row := 0; row < rows; row++ {
		for col := 0; col < cols; col++ {
			g := term.Cell(row, col)
			if g == nil {
				continue
			}

			// Skip the spacer cell of a wide character.
			if g.IsWideSpacer() {
				continue
			}

			bgStr := resolveColor(g.Bg, true)
			fgStr := resolveColor(g.Fg, false)
			bold := g.Flags&1 != 0

			ch := g.Char
			x := padX + col*cellW
			y := padY + row*cellH
			width := cellW
			if g.IsWide() {
				width = cellW * 2
			}

			// Background rect — omit if equal to default background
			if bgStr != defaultBGColor {
				fmt.Fprintf(&sb, `<rect x="%d" y="%d" width="%d" height="%d" fill="%s"/>`,
					x, y, width, cellH, bgStr)
			}

			// Text — only for non-space, non-zero characters.
			if ch != 0 && ch != ' ' {
				escaped := html.EscapeString(string(ch))
				baseline := y + cellH - 4
				if bold {
					fmt.Fprintf(&sb,
						`<text x="%d" y="%d" fill="%s" font-weight="bold" textLength="%d" lengthAdjust="spacingAndGlyphs" clip-path="url(#r%d)">%s</text>`,
						x, baseline, fgStr, width, row, escaped)
				} else {
					fmt.Fprintf(&sb,
						`<text x="%d" y="%d" fill="%s" textLength="%d" lengthAdjust="spacingAndGlyphs" clip-path="url(#r%d)">%s</text>`,
						x, baseline, fgStr, width, row, escaped)
				}
			}
		}
	}

	sb.WriteString("</svg>")
	return sb.String()
}

// renderToPNG converts the current headless terminal state to a PNG image as bytes.
// scale allows for high-resolution exports.
// fontAscii and fontCJK are optional paths to custom font files.
func renderToPNG(term *headlessterm.Terminal, cols, rows int, scale float64, fontAscii, fontCJK string) ([]byte, error) {
	if scale <= 0 {
		scale = 1.0
	}

	scW := float64(cellW) * scale
	scH := float64(cellH) * scale
	scPadX := float64(padX) * scale
	scPadY := float64(padY) * scale
	scFontSize := float64(fontSize) * scale

	imgW := int(float64(cols)*scW + scPadX*2)
	imgH := int(float64(rows)*scH + scPadY*2)

	dc := gg.NewContext(imgW, imgH)

	// Background
	dc.SetHexColor(defaultBGColor)
	dc.Clear()

	// Load Fonts
	var face, faceBold, cjkFace font.Face
	var err error

	// 1. ASCII Font
	if fontAscii != "" {
		fmt.Printf("Using custom ASCII font: %s\n", fontAscii)
		face, err = loadFontFaceFromFile(fontAscii, scFontSize)
		if err != nil {
			return nil, fmt.Errorf("failed to load custom ASCII font: %v", err)
		}
		faceBold = face
	} else {
		// Default to embedded Menlo
		face, err = loadFontFaceFromBytes(embeddedASCIIFont, scFontSize)
		if err != nil {
			return nil, fmt.Errorf("failed to load embedded ASCII font: %v", err)
		}
		faceBold = face
	}

	// 2. CJK Font
	if fontCJK != "" {
		fmt.Printf("Using custom CJK font: %s\n", fontCJK)
		cjkFace, err = loadFontFaceFromFile(fontCJK, scFontSize)
		if err != nil {
			return nil, fmt.Errorf("failed to load custom CJK font: %v", err)
		}
	} else if len(embeddedCJKFont) > 0 {
		// Log once
		cjkFace, _ = loadFontFaceFromBytes(embeddedCJKFont, scFontSize)
	} else {
		cjkFace = face
	}

	if fontAscii == "" && fontCJK == "" {
		fmt.Println("Using embedded fonts: Menlo (ASCII) and WenQuanYi Micro Hei Mono (CJK)")
	}

	dc.SetFontFace(face)

	for row := 0; row < rows; row++ {
		for col := 0; col < cols; col++ {
			g := term.Cell(row, col)
			if g == nil {
				continue
			}

			if g.IsWideSpacer() {
				continue
			}

			bgStr := resolveColor(g.Bg, true)
			fgStr := resolveColor(g.Fg, false)
			bold := g.Flags&1 != 0

			ch := g.Char
			x := scPadX + float64(col)*scW
			y := scPadY + float64(row)*scH
			width := scW
			if g.IsWide() {
				width = scW * 2
			}

			// Background rect
			if bgStr != defaultBGColor {
				dc.SetHexColor(bgStr)
				dc.DrawRectangle(x, y, width, scH)
				dc.Fill()
			}

			// Text
			if ch != 0 && ch != ' ' {
				dc.SetHexColor(fgStr)
				
				currentFace := face
				if bold {
					currentFace = faceBold
				}
				if g.IsWide() || ch > 127 {
					currentFace = cjkFace
				}
				dc.SetFontFace(currentFace)

				// Baseline adjustment
				baseline := y + scH - (5 * scale)
				dc.DrawString(string(ch), x, baseline)
			}
		}
	}

	var buf bytes.Buffer
	err = dc.EncodePNG(&buf)
	if err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}
