#!/usr/bin/env python3
# AI-hint: Native room capture patch ordering and regression coverage gate; actual shader pixels and deterministic lease tests run through CTest in the private builder.
# AI-related: usr/share/mios/hyprland/patches/hypr3d-mios-dock-capture.patch, usr/share/mios/mios.toml
# AI-functions: added, DockCapture
import pathlib
import re
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCH = "usr/share/mios/hyprland/patches/hypr3d-mios-dock-capture.patch"


def added(path):
    text = (ROOT / PATCH).read_text()
    section = text.split("diff --git a/" + path + " b/" + path + "\n", 1)[1]
    section = section.split("\ndiff --git ", 1)[0]
    return "\n".join(line[1:] for line in section.splitlines() if line.startswith("+") and not line.startswith("+++"))


class DockCapture(unittest.TestCase):
    def test_single_trailing_patch(self):
        config = tomllib.loads((ROOT / "usr/share/mios/mios.toml").read_text())
        patches = config["desktop"]["hyprland"]["hypr3d"]["patches"]
        self.assertEqual(patches.count(PATCH), 1)
        self.assertEqual(patches[-3:], [
            "usr/share/mios/hyprland/patches/hypr3d-mios-room-input.patch",
            "usr/share/mios/hyprland/patches/hypr3d-mios-input-transitions.patch",
            PATCH,
        ])

    def test_native_regressions_are_built(self):
        cmake = added("CMakeLists.txt")
        self.assertIn("add_test(NAME mios-dock-capture-feedback", cmake)
        self.assertIn("TIMEOUT 5", cmake)
        lease = added("tests/mios-dock-capture-feedback.cpp")
        for case in ("heartbeat preserves phase", "invalid output clears", "invalid color clears",
                     "invalid width clears", "invalid period clears", "invalid lease clears",
                     "deadline exclusive", "lease clears at deadline", "native fullscreen flat",
                     "exit morph visible", "enter before first frame visible"):
            self.assertIn(case, lease)
        pixels = added("tests/mios-bsp-regression.cpp")
        self.assertIn("compileShader(GL_FRAGMENT_SHADER,materialShader.c_str())", pixels)
        self.assertIn("materialText.find(marker)", pixels)
        for case in ("rounded corner clips", "clearing frame", "HUD/background unaffected",
                     "p->dockCaptureColor", "p->dockCaptureGlow"):
            self.assertIn(case, pixels)

    def test_feedback_shares_native_material(self):
        renderer = added("src/Render/GLScene.cpp")
        self.assertIn("miosDockCapture(vec4(rgb,a),edge_coord)", renderer)
        self.assertIn("getCornerSDF(coord)", renderer)
        self.assertIn("front.dockCaptureGlow = back.dockCaptureGlow = poly.dockCaptureGlow", renderer)
        self.assertIn("ordered[j].dockCaptureGlow == ordered[i].dockCaptureGlow", renderer)
        self.assertNotIn("glDisable(GL_DEPTH_TEST)", renderer)
        self.assertNotIn("projectedBounds", renderer)
        self.assertGreaterEqual(renderer.count("glUniform2f(m_dockCaptureGlow,0.f,0.f)"), 5)

    def test_native_glow_orbits_instead_of_uniform_pulse(self):
        source = added("src/main.cpp")
        self.assertIn("1.f+g_dockCaptureFeedback.orbitPhase(now)", source)
        shader = added("src/Render/GLScene.cpp")
        self.assertIn("atan(edge.x,-edge.y)", shader)
        self.assertIn("dot(edge,edge)>0.00000001", shader)
        self.assertIn("orbit<0.12", shader)
        self.assertIn("orbit<0.4", shader)
        self.assertIn("orbit<0.9", shader)
        self.assertIn("uDockCaptureColor.a*comet*uColor.a", shader)
        pixels = added("tests/mios-bsp-regression.cpp")
        self.assertIn("circling head moves to next side", pixels)
        self.assertIn("previous side is no longer lit", pixels)
        self.assertIn("finite atan at centre", pixels)
        phase = added("tests/mios-dock-capture-feedback.cpp")
        for case in ("quarter orbit", "phase wraps after full orbit", "normalized phase before wrap",
                     "heartbeat preserves wrapped orbit phase"):
            self.assertIn(case, phase)

    def test_same_frame_layer_filter_is_wired(self):
        source = added("src/main.cpp")
        self.assertIn("syncDockCaptureLayerFactors(mon)", source)
        self.assertIn("MiosDockCapture::suppressFlatLayer(", source)
        self.assertIn("layer->m_monitor.lock()!=mon", source)
        self.assertIn("PHLLSREF layer", source)
        self.assertIn("clearDockCaptureLayerFactors()", source)
        self.assertIn("MiosDockCapture::removeFactor(layer->alpha().all(),item.factor)", source)
        self.assertNotIn("setValueAndWarp", source)
        self.assertNotIn("createFunctionHook", source)
        self.assertIn('LS->m_namespace=="mios-dock-capture-glow"', added("src/HyprlandCompat/WindowsCompat.cpp"))
        regression = added("tests/mios-dock-capture-feedback.cpp")
        self.assertIn("same-frame layer suppression before shell heartbeat", regression)
        self.assertIn("factor identity preserves native fade and later foreign factors", regression)
        self.assertIn("unmapped layer never revived", regression)

    def test_minimal_false_precedes_payload_validation(self):
        source = added("src/main.cpp")
        self.assertLess(source.index("if (!active)"), source.index('field("width_px")'))
        self.assertIn("lua_rawget(L,1)", source)
        self.assertIn("g_dockCaptureFeedback.expire", source)
        self.assertRegex(source, r"g_dockCaptureFeedback.clear\(\);\n\s*if \(g_dockCapturePainted\) damageCurrentMonitor")
        self.assertIn("g_dockCapturePainted || visibleWindow", source)


if __name__ == "__main__":
    unittest.main()
