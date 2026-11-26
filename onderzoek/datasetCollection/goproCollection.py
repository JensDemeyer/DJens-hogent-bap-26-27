import argparse
import json
import re
from pathlib import Path

import cv2

_SEQ_RE = re.compile(r"_(\d+)$")

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "raw_brood"
CLASSES_FILE = BASE_DIR / "classes.json"

DEFAULT_CLASSES = [
    ("Wit Brood", "Brood_wit"),
    ("Bruin Brood", "Brood_bruin"),
    ("Volkoren", "Brood_volkoren"),
    ("Meergranen", "Brood_meergranen"),
]

CONDITIONS = [
    ("schoon", "Schoon (geen ruis)"),
    ("licht", "Lichtvariatie"),
    ("occlusie", "Occlusie (hand/voorwerp)"),
]

FLASH_FRAMES = 8


def slugify(text: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip()).strip("_").lower()
    return f"Brood_{clean}" if clean else "Brood_onbekend"


def load_classes() -> list[tuple[str, str]]:
    if CLASSES_FILE.exists():
        data = json.loads(CLASSES_FILE.read_text(encoding="utf-8"))
        if data:
            return [(item[0], item[1]) for item in data]
    save_classes(DEFAULT_CLASSES)
    return list(DEFAULT_CLASSES)


def save_classes(classes: list[tuple[str, str]]) -> None:
    CLASSES_FILE.write_text(json.dumps(classes, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=None)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--window-height", type=int, default=700)
    parser.add_argument("--target-clean", type=int, default=45)
    parser.add_argument("--target-noise", type=int, default=15)
    return parser.parse_args()


def open_gopro_camera(requested_idx: int | None, height: int) -> cv2.VideoCapture:
    indices_to_try = [requested_idx] if requested_idx is not None else [1, 2, 0, 3]
    for idx in indices_to_try:
        cap = cv2.VideoCapture(idx)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, round(height * 16 / 9))
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                print(f"Verbonden met camera op index {idx} ({int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}).")
                return cap
        cap.release()
    raise RuntimeError("Kon geen actieve GoPro/camera vinden. Controleer of de GoPro Webcam-app draait en verbonden is.")


def class_folder(slug: str, condition_slug: str) -> Path:
    return RAW_DIR / slug / condition_slug


def ensure_folders(classes: list[tuple[str, str]]) -> None:
    for _, slug in classes:
        for cond_slug, _ in CONDITIONS:
            class_folder(slug, cond_slug).mkdir(parents=True, exist_ok=True)


def next_seq_number(folder: Path, prefix: str) -> int:
    highest = 0
    for p in folder.glob(f"{prefix}_*.jpg"):
        match = _SEQ_RE.search(p.stem)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def count_photos(folder: Path, prefix: str) -> int:
    return len(list(folder.glob(f"{prefix}_*.jpg")))


def draw_overlay(frame, display_name, condition_label, count, target, flash):
    h, w = frame.shape[:2]
    overlay = frame.copy()

    cv2.rectangle(overlay, (0, 0), (w, 110), (20, 20, 20), -1)
    cv2.putText(overlay, display_name, (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)
    progress_color = (0, 200, 0) if count >= target else (0, 200, 255)
    cv2.putText(
        overlay, f"{condition_label}: {count}/{target}", (20, 85),
        cv2.FONT_HERSHEY_SIMPLEX, 0.8, progress_color, 2,
    )

    legend = "SPATIE=foto | A/D=klasse | N=nieuwe klasse | C=conditie | Z=undo | Q=stop"
    cv2.rectangle(overlay, (0, h - 40), (w, h), (20, 20, 20), -1)
    cv2.putText(overlay, legend, (15, h - 13), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

    if flash is not None:
        text, color, _countdown = flash
        cv2.rectangle(overlay, (0, 0), (w - 1, h - 1), color, 12)
        text_w = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.2, 3)[0][0]
        cv2.putText(overlay, text, (w // 2 - text_w // 2, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

    return overlay


def prompt_new_class(classes: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], int]:
    print("\n--- NIEUWE BROODCATEGORIE ---")
    new_name = input("Voer de naam van het brood in (laat leeg om te annuleren): ").strip()
    if not new_name:
        print("Geannuleerd.\n")
        return classes, -1

    new_slug = slugify(new_name)
    for idx, (_, existing_slug) in enumerate(classes):
        if existing_slug == new_slug:
            print(f"Categorie '{new_name}' bestaat al, overgeschakeld naar bestaande categorie.\n")
            return classes, idx

    classes.append((new_name, new_slug))
    save_classes(classes)
    ensure_folders(classes)
    print(f"Toegevoegd en geselecteerd: {new_name} -> map '{new_slug}'\n")
    return classes, len(classes) - 1


def main():
    args = parse_args()
    classes = load_classes()
    ensure_folders(classes)

    cap = open_gopro_camera(args.camera, args.height)

    class_idx = 0
    condition_idx = 0
    flash = None
    last_saved_path = None
    window_name = "Brood dataset - GoPro Hero 13"

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, round(args.window_height * 16 / 9), args.window_height)

    print("Opnametool gestart. Gebruik SPATIE om te fotograferen, N voor een nieuwe categorie.")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Verbinding met GoPro verbroken.")
                break

            display_name, slug = classes[class_idx]
            cond_slug, cond_label = CONDITIONS[condition_idx]
            folder = class_folder(slug, cond_slug)
            prefix = f"{slug}_{cond_slug}"
            count = count_photos(folder, prefix)
            target = args.target_clean if cond_slug == "schoon" else args.target_noise

            shown = draw_overlay(frame, display_name, cond_label, count, target, flash)
            cv2.imshow(window_name, shown)
            if flash is not None:
                text, color, countdown = flash
                flash = (text, color, countdown - 1) if countdown > 1 else None

            key = cv2.waitKey(1) & 0xFF

            if key == ord(" "):
                seq = next_seq_number(folder, prefix)
                out_path = folder / f"{prefix}_{seq:03d}.jpg"
                cv2.imwrite(str(out_path), frame)
                print(f"Opgeslagen: {out_path.relative_to(RAW_DIR)}  ({display_name} / {cond_label})")
                last_saved_path = out_path
                flash = ("OPGESLAGEN", (0, 220, 0), FLASH_FRAMES)

            elif key == ord("n"):
                classes, new_idx = prompt_new_class(classes)
                if new_idx != -1:
                    class_idx = new_idx
                    condition_idx = 0
                    flash = (f"NIEUW: {classes[class_idx][0]}", (255, 200, 0), FLASH_FRAMES)

            elif key == ord("z"):
                if last_saved_path is not None and last_saved_path.exists():
                    deleted_name = last_saved_path.name
                    last_saved_path.unlink()
                    print(f"Verwijderd: {deleted_name}")
                    flash = ("VERWIJDERD", (0, 0, 220), FLASH_FRAMES)
                    last_saved_path = None
                else:
                    print("Niets om ongedaan te maken.")

            elif key == ord("d"):
                class_idx = (class_idx + 1) % len(classes)
                condition_idx = 0
            elif key == ord("a"):
                class_idx = (class_idx - 1) % len(classes)
                condition_idx = 0
            elif key == ord("c"):
                condition_idx = (condition_idx + 1) % len(CONDITIONS)
            elif key == ord("q") or key == 27:
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    print("\nOverzicht:")
    print(f"  {'='*60}")
    total = 0
    for display_name, slug in classes:
        counts = []
        for cond_slug, cond_label in CONDITIONS:
            n = count_photos(class_folder(slug, cond_slug), f"{slug}_{cond_slug}")
            counts.append(f"{cond_label}={n}")
            total += n
        print(f"  {display_name:.<24} {' | '.join(counts)}")
    print(f"  {'='*60}")
    print(f"  Totaal.......................... {total}")


if __name__ == "__main__":
    main()