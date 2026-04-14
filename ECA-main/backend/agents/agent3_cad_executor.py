"""
AGENT 3 — CAD EXECUTOR
Modifies real FreeCAD .FCStd model files via headless subprocess.
Exports updated STEP. Falls back to matplotlib render if FreeCAD unavailable.
"""
import subprocess, json, os, time, tempfile, base64, io, shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB = Path(__file__).parent.parent / "database"
CAD_DIR = Path(__file__).parent.parent / "cad_models"
CAD_DIR.mkdir(exist_ok=True)

class Agent3CADExecutor:
    name = "CAD EXECUTOR"
    description = "Modifies FreeCAD model, exports STEP, renders before/after"

    def __init__(self):
        self.freecad_cmd = os.getenv("FREECAD_CMD", r"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe")

    def run(self, parsed: dict, parts_db: dict) -> dict:
        start = time.time()
        changes = parsed.get("changes") or []
        if not changes:
            return {"modified_parts": [], "cad_method": "none", "render_base64": None,
                    "_agent": self.name, "_time_seconds": 0}

        results = []
        step_files = []
        freecad_available = os.path.isfile(self.freecad_cmd)
        cad_method = "freecad" if freecad_available else "simulation"

        # --- 🛡️ GEOMETRIC INTEGRITY GUARDRAIL ---
        for c in changes:
            pid = c.get("part_id", "PART-001")
            part = parts_db.get(pid, {})
            dims = part.get("dimensions", {})
            od = dims.get("outer_diameter_mm", 150)
            id_ = dims.get("inner_diameter_mm", 126)
            param = c.get("parameter", "")
            if "outer_diameter" in param: od = c.get("new_value", od)
            if "inner_diameter" in param: id_ = c.get("new_value", id_)
            if id_ >= od:
                return {
                    "success": False, "overall_safe": False,
                    "error": f"CRITICAL GEOMETRY ERROR: Inner Diameter ({id_}mm) cannot be >= Outer Diameter ({od}mm).",
                    "modified_parts": [], "cad_method": "aborted_safety_fail",
                    "render_base64": None, "_agent": self.name, "_time_seconds": 0
                }

        for c in changes:
            pid = c.get("part_id", "PART-001")
            part = parts_db.get(pid, {})
            param = c.get("parameter", "")
            new_val = c.get("new_value")
            cur_val = c.get("current_value")
            feature_map = part.get("cad_feature_map", {})
            cad_feature = feature_map.get(param, c.get("cad_feature", param))

            model_file = CAD_DIR / f"{pid}.FCStd"
            step_file = CAD_DIR / f"{pid}_updated.step"

            cad_result = {"part_id": pid, "part_name": part.get("name", pid),
                          "parameter": param, "cad_feature": cad_feature,
                           "original_value": cur_val, "new_value": new_val,
                          "cad_method": cad_method}

            if freecad_available:
                # First ensure parametric model exists
                if not model_file.exists():
                    self._create_parametric_model(pid, part, model_file)

                # Run FreeCAD headless modification
                fc_result = self._run_freecad(model_file, cad_feature, new_val, step_file, pid)
                cad_result.update(fc_result)
                if step_file.exists():
                    step_files.append(str(step_file))
            else:
                # Simulation fallback for lightning-fast demo
                cad_result["status"] = "simulated"
                cad_result["message"] = f"Simulated: {cad_feature} = {cur_val} -> {new_val}"
                
                # Auto-copy the valve assembly to PART-001 so the "View Modified Model" button shows the assembly instead of a broken cylinder
                import shutil
                assembly_src = CAD_DIR / "valve_assembly.FCStd"
                if assembly_src.exists():
                    shutil.copy2(str(assembly_src), str(model_file))

            # --- 📦 VOLUMETRIC DELTA CALCULATION ---
            import math
            h = 100.0 # Standard 100mm segment for comparison
            
            # Determine if we are dealing with a hollow pipe or a solid cylinder (stem)
            is_stem = "stem" in param.lower() or "stem" in cad_feature.lower()
            
            if is_stem:
                # Solid Cylinder: V = pi * r^2 * h
                rad_o = cur_val / 2 if cur_val else 11.0
                rad_u = new_val / 2
                vol_o = math.pi * (rad_o**2) * h
                vol_u = math.pi * (rad_u**2) * h
            else:
                # Hollow Pipe: V = pi * (R^2 - r^2) * h
                # Use current OD/ID from DB as baseline
                od_o = part.get("dimensions", {}).get("outer_diameter_mm", 150)
                id_o = part.get("dimensions", {}).get("inner_diameter_mm", 126)
                
                od_u = new_val if "outer_diameter" in param else od_o
                id_u = new_val if ("inner_diameter" in param or "bore" in param) else id_o
                if "wall_thickness" in param:
                     id_u = od_u - (2 * new_val)

                vol_o = math.pi * ((od_o/2)**2 - (id_o/2)**2) * h
                vol_u = math.pi * ((od_u/2)**2 - (id_u/2)**2) * h

            vol_delta = vol_o - vol_u # Positive means material was REMOVED
            
            cad_result.update({
                "vol_removed_mm3": round(vol_delta, 1),
                "mass_reduction_pct": round((vol_delta / vol_o) * 100, 1) if vol_o > 0 else 0
            })

            # Validation checks
            checks = self._validate(c, part)
            cad_result["validation"] = {
                "overall": "PASS" if all(ch["status"] == "PASS" for ch in checks) else "FAIL",
                "checks": checks
            }
            results.append(cad_result)

        # Generate side-by-side render
        render_b64 = self._generate_render(parsed, parts_db)

        return {
            "modified_parts": results,
            "step_files": step_files,
            "cad_method": cad_method,
            "render_base64": f"data:image/png;base64,{render_b64}" if render_b64 else None,
            "_agent": self.name,
            "_time_seconds": round(time.time() - start, 2)
        }

    def _create_parametric_model(self, pid, part, model_file):
        """Copy the pre-built valve assembly as the base model."""
        assembly_src = CAD_DIR / "valve_assembly.FCStd"
        if assembly_src.exists():
            shutil.copy2(str(assembly_src), str(model_file))
            print(f"  [CAD] Copied valve assembly -> {model_file}")
        else:
            # Fallback: build simple model if assembly doesn't exist
            dims = part.get("dimensions", {})
            od = dims.get("outer_diameter_mm", 150)
            wall = dims.get("wall_thickness_mm", 12)
            length = dims.get("length_mm", 300)
            script = f'''
import FreeCAD, Part, os
doc = FreeCAD.newDocument("{pid}")
outer = doc.addObject("Part::Cylinder", "OuterCylinder")
outer.Radius = FreeCAD.Units.Quantity("{od/2} mm")
outer.Height = FreeCAD.Units.Quantity("{length} mm")
inner = doc.addObject("Part::Cylinder", "InnerCylinder")
inner.Radius = FreeCAD.Units.Quantity("{(od/2) - wall} mm")
inner.Height = FreeCAD.Units.Quantity("{length + 2} mm")
inner.Placement.Base.z = -1
cut = doc.addObject("Part::Cut", "ValveBody")
cut.Base = outer; cut.Tool = inner
doc.recompute()
doc.saveCopy("{str(model_file).replace(chr(92), '/')}")
print("MODEL_CREATED_OK")
os._exit(0)
'''
            self._exec_freecad_script(script)

    def _run_freecad(self, model_file, feature_name, new_value, step_file, pid):
        """Run FreeCAD headlessly to modify valve assembly and export STEP."""
        model_path = str(model_file).replace("\\", "/")
        step_path = str(step_file).replace("\\", "/")

        script = f'''
import FreeCAD
import Part
import os

doc = FreeCAD.openDocument("{model_path}")

print("Objects in model:")
for obj in doc.Objects:
    r_str = ""
    if hasattr(obj, "Radius"):
        r_str = f" Radius={{float(obj.Radius)}}"
    print(f"  {{obj.Name}} ({{obj.Label}}) {{obj.TypeId}}{{r_str}}")

# Find valve body inner cylinder (bore)
vb_inner = doc.getObject("ValveBody_Inner")
vb_outer = doc.getObject("ValveBody_Outer")
pL_inner = doc.getObject("PipeLeft_Inner")
pR_inner = doc.getObject("PipeRight_Inner")
disc     = doc.getObject("ButterflyDisc")
stem     = doc.getObject("ValveStem")
handle   = doc.getObject("Handle")

# Also check for simple model fallback
if not vb_inner:
    vb_inner = doc.getObject("InnerDiameter") or doc.getObject("InnerCylinder")
    vb_outer = doc.getObject("OuterDiameter") or doc.getObject("OuterCylinder")

if vb_outer and vb_inner:
    outer_r = float(vb_outer.Radius)
    inner_r = float(vb_inner.Radius)
    wall_before = outer_r - inner_r
    print(f"\\nBEFORE: OD={{outer_r*2}}, ID={{inner_r*2}}, Wall={{wall_before}}")

    # --- DIMENSION CALCULATION ---
    new_inner_r = inner_r # default to current
    new_outer_r = outer_r # default to current
    
    feat = "{feature_name}".lower()
    # Safety: ensure new_value is a valid number
    try:
        val = float({new_value})
    except:
        val = outer_r - inner_r # default to current wall

    if "wall" in feat or "thickness" in feat:
        # --- SMART WALL LOGIC: Reduce outward shell (OD) while preserving the ID/flow-path.
        # This keeps the assembly connected and the pipe interface intact.
        new_outer_r = inner_r + val
        new_inner_r = inner_r # Keep Bore constant to preserve pipe interface
        print(f"  MODE: Smart Wall Thickness -> {new_value} (New Outer R: {{new_outer_r}}, New Inner R: {{new_inner_r}})")
    elif "stem" in feat:
        if stem:
            if "length" in feat or "height" in feat:
                stem.Height = FreeCAD.Units.Quantity(f"{{val}} mm")
                print(f"  MODE: Valve Stem Length -> {new_value} (New Stem Height: {{val}})")
            else:
                stem.Radius = FreeCAD.Units.Quantity(f"{{val / 2}} mm")
                print(f"  MODE: Valve Stem Diameter -> {new_value} (New Stem R: {{val / 2}})")
        else:
            print("  WARNING: ValveStem object not found")
    elif "inner" in feat or "id" in feat or "bore" in feat:
        new_inner_r = val / 2
        print(f"  MODE: Inner Diameter -> {new_value} (New Inner R: {{new_inner_r}})")
    elif "outer" in feat or "od" in feat:
        new_outer_r = val / 2
        print(f"  MODE: Outer Diameter -> {new_value} (New Outer R: {{new_outer_r}})")
    else:
        # Smart Heuristic
        if val > (outer_r * 0.8) and val < (outer_r * 1.5):
            new_outer_r = val / 2
        elif val < 50 and "stem" not in feat: # Don't guess wall if it looks like a stem change
             new_inner_r = outer_r - val
        else:
             new_inner_r = val / 2

    # --- SAFETY CLIPPING ---
    if new_inner_r <= 0: new_inner_r = inner_r  # don't allow zero
    if new_outer_r <= new_inner_r: new_outer_r = new_inner_r + 5.0 # ensure OD > ID

    # --- APPLY UPDATES (MASTER SYNCHRONIZATION) ---
    # 1. Update all Outer Shells
    for o_name in ["ValveBody_Outer", "PipeLeft_Outer", "PipeRight_Outer", "OuterDiameter", "OuterCylinder"]:
        o = doc.getObject(o_name)
        if o and hasattr(o, "Radius"): o.Radius = FreeCAD.Units.Quantity(f"{{new_outer_r}} mm")
    
    # 2. Update all Bores (Ensure pipe/valve alignment)
    for obj_name in ["InnerDiameter", "ValveBody_Inner", "PipeLeft_Inner", "PipeRight_Inner",
                     "FlangeLeft_Inner", "FlangeRight_Inner", "InnerCylinder", "Bore"]:
        obj = doc.getObject(obj_name)
        if obj and hasattr(obj, "Radius"):
            obj.Radius = FreeCAD.Units.Quantity(f"{{new_inner_r}} mm")
    
    # 3. Update Butterfly Disc (Must fit new bore)
    if disc:
        disc_r = max(5.0, new_inner_r - 1.5)
        disc.Radius = FreeCAD.Units.Quantity(f"{{disc_r}} mm")

    # 4. Maintain Stem/Handle alignment relative to the new Outer Shell
    if stem:
        if not ("stem" in feat and ("length" in feat or "height" in feat)):
            stem.Height = (new_outer_r * 2) + 60
        stem.Placement.Base.y = -(new_outer_r + 30)
    if handle:
        handle.Placement.Base.y = new_outer_r + 25

    doc.recompute()

    wall_after = float(vb_outer.Radius) - float(vb_inner.Radius)
    print(f"AFTER: OD={{float(vb_outer.Radius)*2}}, ID={{float(vb_inner.Radius)*2}}, Wall={{wall_after}}")

    # Export ALL visible shapes as complete assembly STEP
    shapes = [o for o in doc.Objects if hasattr(o, "Shape") and not o.Shape.isNull()
              and o.TypeId not in ("Part::Cylinder", "Part::Box")]
    if not shapes:
        shapes = [o for o in doc.Objects if hasattr(o, "Shape") and not o.Shape.isNull()]
    if shapes:
        Part.export(shapes, "{step_path}")
        print("STEP_EXPORTED_OK")

    doc.save()
    print("FREECAD_COMPLETE")
else:
    print("ERROR: Could not find valve body objects")

os._exit(0)
'''
        stdout = self._exec_freecad_script(script)
        success = "FREECAD_COMPLETE" in stdout
        step_exported = "STEP_EXPORTED_OK" in stdout

        return {
            "status": "success" if success else "error",
            "step_exported": step_exported,
            "step_file": step_path if step_exported else None,
            "freecad_log": stdout[-500:] if stdout else ""
        }

    def _exec_freecad_script(self, script_content):
        """Execute a FreeCAD script via subprocess."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, dir=str(CAD_DIR)) as f:
            f.write(script_content)
            script_path = f.name

        try:
            result = subprocess.run(
                [self.freecad_cmd, script_path],
                capture_output=True, text=True, timeout=15,
                cwd=str(CAD_DIR)
            )
            return result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return "TIMEOUT"
        except Exception as e:
            return f"ERROR: {str(e)}"
        finally:
            try: os.unlink(script_path)
            except: pass

    def _validate(self, change, part):
        """Engineering validation checks."""
        checks = []
        param = change.get("parameter", "")
        new_val = change.get("new_value")

        if "wall_thickness" in param and new_val and part.get("category") == "pressure_retaining":
            od = part["dimensions"].get("outer_diameter_mm", 150)
            dp = part.get("design_pressure_bar", 150)
            S = 138  # SS316 allowable stress MPa
            pmax = (2 * S * (new_val / 1000)) / (od / 1000) * 10
            sf = pmax / dp
            checks.append({
                "check": "Barlow Pressure Analysis (P=2St/D)",
                "status": "PASS" if sf >= 2.5 else "FAIL",
                "detail": f"Pmax={pmax:.0f} bar, SF={sf:.2f} ({'>=' if sf >= 2.5 else '<'} 2.5 ASME VIII)"
            })
            checks.append({
                "check": "Minimum Wall Thickness (ASME VIII)",
                "status": "PASS" if new_val >= 6.0 else "FAIL",
                "detail": f"{new_val}mm {'>=' if new_val >= 6 else '<'} 6mm minimum"
            })

            # OD/ID consistency
            new_id = od - 2 * new_val
            checks.append({
                "check": "OD/ID Geometric Consistency",
                "status": "PASS" if new_id > 0 else "FAIL",
                "detail": f"OD={od}mm, new_wall={new_val}mm, new_ID={new_id:.1f}mm"
            })

        return checks

    def _generate_render(self, parsed, parts_db):
        """Generate high-fidelity side-by-side comparison with ghost overlay."""
        changes = parsed.get("changes", [])
        if not changes: return None

        pid = changes[0].get("part_id", "PART-001")
        part = parts_db.get(pid, {})
        orig = part.get("dimensions", {}).copy()
        updated = orig.copy()
        for c in changes:
            if c.get("part_id") == pid and c.get("parameter") and c.get("new_value") is not None:
                param = c["parameter"]
                new_val = c["new_value"]
                updated[param] = new_val

                # Synchronize geometric relationships for the renderer
                if "outer_diameter" in param:
                    id_val = updated.get("inner_diameter_mm", orig.get("inner_diameter_mm", 126))
                    updated["wall_thickness_mm"] = (new_val - id_val) / 2
                elif "inner_diameter" in param:
                    od_val = updated.get("outer_diameter_mm", orig.get("outer_diameter_mm", 150))
                    updated["wall_thickness_mm"] = (od_val - new_val) / 2
                elif "wall_thickness" in param:
                    od_val = updated.get("outer_diameter_mm", orig.get("outer_diameter_mm", 150))
                    updated["inner_diameter_mm"] = od_val - (2 * new_val)

        fig, axes = plt.subplots(1, 2, figsize=(16, 8), facecolor="#0D1117")
        fig.suptitle(f"CAD IMPACT ANALYSIS — {part.get('name', pid)} ({pid})",
                     color="#58a6ff", fontsize=16, fontweight="bold", fontfamily="monospace", y=0.98)

        # Common scaling
        od_max = max(orig.get("outer_diameter_mm", 150), updated.get("outer_diameter_mm", 150))
        id_max = max(orig.get("inner_diameter_mm", 126), updated.get("inner_diameter_mm", 126))
        
        # Consistent scaling: Use the physical dimensions directly in the plot, 
        # and just set the limits based on the OD.
        limit = (od_max / 2) * 1.5  # 50% padding for labels
        scale = 1.0 # Remove arbitrary scaling factor to prevent "off-screen" circles

        # Baseline dimensions for comparison
        od_o = orig.get("outer_diameter_mm", 150)
        id_o = orig.get("inner_diameter_mm", od_o - 2 * orig.get("wall_thickness_mm", 12))
        wall_o = (od_o - id_o) / 2

        for ax, dims, label, color in zip(axes,
                [orig, updated], ["BASELINE (Before)", "MODIFIED (After)"], ["#3a86ff", "#00f5d4"]):
            ax.set_facecolor("#161b22")
            ax.set_aspect("equal")
            ax.set_xlim(-limit, limit); ax.set_ylim(-limit, limit)
            ax.axis("off")

            # Calc dimensions synchronously
            od_u = dims.get("outer_diameter_mm", 150)
            id_u = dims.get("inner_diameter_mm", od_u - 2 * dims.get("wall_thickness_mm", 12))
            wall_u = (od_u - id_u) / 2

            # 👻 GHOST OVERLAY (Only on the Adjusted side)
            if label == "MODIFIED (After)":
                # Draw the original ID as a ghost line to show the gap
                ghost_ri = (id_o / 2) * scale
                ax.add_patch(plt.Circle((0, 0), ghost_ri, fill=False, color="#f85149", alpha=0.4, 
                                        linestyle="--", linewidth=1.5, label="Original Bore"))
                
                # Highlight the 2mm gap if thickness changed
                if wall_u != wall_o:
                    ri_u = (id_u / 2) * scale
                    ri_o = (id_o / 2) * scale
                    diff_rect = plt.Rectangle((min(ri_u, ri_o), -10), abs(ri_u - ri_o), 20, 
                                            color="#ffd60a", alpha=0.6, label="Delta Area")
                    ax.add_patch(diff_rect)

            # Main Outlines
            ro, ri = (od_u / 2) * scale, (id_u / 2) * scale
            ax.add_patch(plt.Circle((0, 0), ro, fill=True, facecolor=color, alpha=0.15, edgecolor=color, linewidth=3))
            ax.add_patch(plt.Circle((0, 0), ri, fill=True, facecolor="#0D1117", alpha=1.0, edgecolor="#8b949e", linewidth=1.5, linestyle="--"))

            # Labels and Callouts
            ax.set_title(label, color=color, fontsize=14, fontfamily="monospace", fontweight="bold", pad=20)
            
            # Wall thickness arrow
            ax.annotate("", xy=(ro, 40), xytext=(ri, 40), arrowprops=dict(arrowstyle="<->", color="#ffd60a", lw=2.5))
            ax.text((ro + ri) / 2, 55, f"t = {wall_u:.1f}mm", color="#ffd60a", ha="center", fontsize=12, fontweight="bold")

            # Diameter label
            ax.annotate("", xy=(-ro, -50), xytext=(ro, -50), arrowprops=dict(arrowstyle="<->", color=color, lw=1.5))
            ax.text(0, -75, f"OD = {od_u:.1f}mm", color=color, ha="center", fontsize=11)

            # Bore label
            ax.annotate("", xy=(-ri, -110), xytext=(ri, -110), arrowprops=dict(arrowstyle="<->", color="#8b949e", lw=1.5))
            ax.text(0, -135, f"ID = {id_u:.1f}mm", color="#8b949e", ha="center", fontsize=11)

            # Change indicator
            if label == "MODIFIED (After)" and wall_u != wall_o:
                delta = wall_u - wall_o
                ax.text(0, 180, f"DISPLACEMENT: {delta:+.1f}mm", color="#ffd60a", 
                        ha="center", fontsize=14, fontweight="bold", bbox=dict(facecolor="#161b22", alpha=0.8, edgecolor="#ffd60a"))

        plt.tight_layout(rect=[0, 0, 1, 0.94])
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=160, bbox_inches="tight", facecolor="#0D1117")
        plt.close()
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()
