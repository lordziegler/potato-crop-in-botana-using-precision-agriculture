"""Clean the Granja Botana soil-sample sheet for QGIS interpolation (IDW / kriging).

Input : data/raw/Book(Hoja1).csv  (';'-separated, UTF-8 BOM, CRLF, free-text coordinates)
Output: data/clean/soil_samples.csv   one row per unique location, WGS84 lon/lat
        data/clean/soil_samples.gpkg  same points projected to EPSG:9377 (MAGNA-SIRGAS 2018 / Origen-Nacional)
        data/clean/qc_log.csv         every value changed by this script, with the reason

Units are NOT stated in the raw sheet. Assumed (verify against the lab report):
ph_naf, ph_water (unitless); om (%); p (mg/kg); k, al (cmol(+)/kg); ec (unknown).

Run from the project root:  python3 scripts/clean_soil_samples.py
"""
import re
from pathlib import Path

import pandas as pd
from osgeo import gdal

gdal.UseExceptions()

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/raw/Book(Hoja1).csv"
OUT = ROOT / "data/clean"
TARGET_CRS = "EPSG:9377"
NUM = ["ph_naf", "ph_water", "om", "p", "k", "al", "ec"]

cols = ["sample_id", "coords", "crop", *NUM, "team"]
df = pd.read_csv(RAW, sep=";", encoding="utf-8-sig", header=None, skiprows=1,
                 usecols=range(len(cols)), names=cols, dtype=str)
df = df.apply(lambda s: s.str.strip())

# Coordinates: "lat, lon" with mixed separators, decimal commas and stray spaces.
# Longitude is always negative, so split on the minus sign.
lat_lon = df["coords"].map(lambda s: re.sub(r"\s", "", s).split("-"))
df["lat"] = lat_lon.str[0].str.rstrip(",").str.replace(",", ".").astype(float)
df["lon"] = -lat_lon.str[1].str.replace(",", ".").astype(float)

df[NUM] = df[NUM].apply(lambda s: s.str.replace(",", ".").astype(float))
df["crop"] = df["crop"].str.replace(r"\s+", " ", regex=True).str.capitalize().replace({"Maiz": "Maíz"})
df["team"] = "T" + (pd.factorize(df["team"])[0] + 1).astype(str)  # sampling team, names dropped
df.loc[df["team"] == "T0", "team"] = pd.NA

log = []


def fix(mask, col, new, reason):
    """Replace df.loc[mask, col] with `new` (scalar or callable) and record each change."""
    for i in df.index[mask]:
        old = df.at[i, col]
        df.at[i, col] = new(old) if callable(new) else new
        log.append({"sample_id": df.at[i, "sample_id"], "field": col,
                    "raw": old, "clean": df.at[i, col], "reason": reason})


# M23 repeats M22's location (lon -77.1757 is a one-digit typo of M22's -77.2757, lat identical),
# with near-identical values and no team. Keep M22 only.
log.append({"sample_id": "M23", "field": "all", "raw": None, "clean": None,
            "reason": "duplicate of M22 location (lon one-digit typo); dropped, M22 kept"})
df = df[df["sample_id"] != "M23"]

sid = df["sample_id"]
fix(sid == "M15", "lat", 1.15570465, "decimal point missing (115570465)")
fix(sid == "M20", "lat", 1.15597157,
    "digit missing: 1.5597157 lies ~45 km north; 1.15597157 fits the team transect (M19-M21). VERIFY with GPS")
for c in ["p", "k"]:
    fix(df[c] > 1000, c, lambda v: v / 1000,
        "decimal comma read as thousands separator (e.g. '5,871' -> 5871)")
fix(df["om"] < 0.1, "om", lambda v: round(v * 100, 2),
    "ASSUMED reported as fraction, not %; all other samples are 1.6-6.6 %. VERIFY")

df[NUM] = df[NUM].round(4)

assert df["lat"].between(1.15, 1.16).all() and df["lon"].between(-77.28, -77.27).all(), "point outside farm"
assert not df.duplicated(["lat", "lon"]).any()

OUT.mkdir(parents=True, exist_ok=True)
out_cols = ["sample_id", "lat", "lon", "crop", "team", *NUM]
df[out_cols].to_csv(OUT / "soil_samples.csv", index=False)
pd.DataFrame(log).to_csv(OUT / "qc_log.csv", index=False)

gdal.VectorTranslate(
    str(OUT / "soil_samples.gpkg"), str(OUT / "soil_samples.csv"), format="GPKG",
    layerName="soil_samples", srcSRS="EPSG:4326", dstSRS=TARGET_CRS, accessMode="overwrite",
    options=["-oo", "X_POSSIBLE_NAMES=lon", "-oo", "Y_POSSIBLE_NAMES=lat",
             "-oo", "AUTODETECT_TYPE=YES", "-oo", "KEEP_GEOM_COLUMNS=YES"],
)
print(f"{len(df)} points written; {len(log)} QC entries in {OUT / 'qc_log.csv'}")
