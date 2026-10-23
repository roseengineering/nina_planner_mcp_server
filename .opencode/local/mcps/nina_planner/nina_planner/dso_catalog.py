"""Built-in deep-sky catalog for target ranking (see ``tools.sky.best_targets``).

Static J2000 positions, integrated visual magnitudes, and angular sizes for
every Messier object (minus the double star M40 and asterism M73) plus bright
NGC/Caldwell showpieces, chosen to cover every season from northern latitudes
and include the southern showpieces for southern sites.

Magnitudes are integrated (total) visual magnitudes; sizes are major/minor
axes in arcminutes. These are catalog values good enough for ranking and
scheduling -- not for plate-solving; use ``resolve_target`` (SIMBAD) when a
plan needs verified precision coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dso:
    """One catalog deep-sky object.

    ``kind`` is a short type tag: "galaxy", "globular", "open" (open cluster),
    "pn" (planetary nebula), "bn" (bright/diffuse nebula), "snr" (supernova
    remnant), or "cloud" (star cloud).
    """

    name: str
    common: str
    kind: str
    ra_hours: float
    dec_deg: float
    mag: float
    major_arcmin: float
    minor_arcmin: float


CATALOG: tuple[Dso, ...] = (
    # ---- Milky Way clusters: the easiest showpieces ----
    Dso("M6", "Butterfly Cluster", "open", 17.6711, -32.2167, 4.2, 25, 25),
    Dso("M7", "Ptolemy Cluster", "open", 17.8975, -34.7933, 3.3, 30, 30),
    Dso("M11", "Wild Duck Cluster", "open", 18.8514, -6.2697, 6.3, 14, 14),
    Dso("M24", "Sagittarius Star Cloud", "cloud", 18.2833, -18.55, 4.6, 90, 60),
    Dso("M25", "IC 4725", "open", 18.5297, -19.25, 4.6, 36, 36),
    Dso("M34", "NGC 1039", "open", 2.7014, 42.7567, 5.5, 35, 35),
    Dso("M35", "NGC 2168", "open", 6.1483, 24.3333, 5.3, 28, 28),
    Dso("M36", "NGC 1960", "open", 5.605, 34.14, 6.3, 12, 12),
    Dso("M37", "NGC 2099", "open", 5.8717, 32.5533, 6.2, 24, 24),
    Dso("M38", "NGC 1912", "open", 5.4783, 35.84, 7.4, 21, 21),
    Dso("M39", "NGC 7092", "open", 21.8833, 48.2667, 5.5, 32, 32),
    Dso("M41", "NGC 2287", "open", 6.7667, -20.7333, 4.5, 38, 38),
    Dso("M44", "Beehive Cluster", "open", 8.67, 19.6667, 3.7, 95, 95),
    Dso("M45", "Pleiades", "open", 3.79, 24.1167, 1.6, 110, 110),
    Dso("M46", "NGC 2437", "open", 7.6961, -14.8167, 6.1, 27, 27),
    Dso("M47", "NGC 2422", "open", 7.61, -14.5, 4.4, 30, 30),
    Dso("M48", "NGC 2548", "open", 8.23, -5.75, 5.5, 30, 30),
    Dso("M50", "NGC 2323", "open", 7.055, -8.35, 5.9, 16, 16),
    Dso("M52", "NGC 7654", "open", 23.4133, 61.5933, 7.3, 13, 13),
    Dso("M67", "NGC 2682", "open", 8.855, 11.8167, 6.1, 30, 30),
    Dso("M93", "NGC 2447", "open", 7.7367, -23.85, 6.2, 12, 12),
    Dso("M103", "NGC 581", "open", 1.5564, 60.6933, 7.4, 6, 6),
    Dso("NGC 869/884", "Double Cluster", "open", 2.3342, 57.1333, 4.3, 60, 60),
    # ---- Globular clusters ----
    Dso("M2", "NGC 7089", "globular", 21.5575, -0.8167, 6.3, 16, 16),
    Dso("M3", "NGC 5272", "globular", 13.7033, 28.3772, 6.2, 18, 18),
    Dso("M4", "NGC 6121", "globular", 16.3932, -26.5253, 5.9, 36, 36),
    Dso("M5", "NGC 5904", "globular", 15.3092, 2.0811, 5.6, 23, 23),
    Dso("M9", "NGC 6333", "globular", 17.5209, -18.5181, 7.7, 12, 12),
    Dso("M10", "NGC 6254", "globular", 16.9525, -4.1008, 6.6, 15, 15),
    Dso("M12", "NGC 6218", "globular", 16.7906, -1.9583, 6.7, 16, 16),
    Dso("M13", "Great Hercules Cluster", "globular", 16.6947, 36.46, 5.8, 20, 20),
    Dso("M14", "NGC 6402", "globular", 17.6267, -3.25, 7.6, 11, 11),
    Dso("M15", "NGC 7078", "globular", 21.4994, 12.1669, 6.2, 18, 18),
    Dso("M19", "NGC 6273", "globular", 17.0403, -26.2667, 6.8, 14, 14),
    Dso("M22", "NGC 6656", "globular", 18.6067, -23.9033, 5.1, 32, 32),
    Dso("M28", "NGC 6626", "globular", 18.4092, -24.8633, 6.8, 11, 11),
    Dso("M30", "NGC 7099", "globular", 21.6728, -23.1033, 7.2, 12, 12),
    Dso("M53", "NGC 5024", "globular", 13.2153, 18.1667, 7.6, 13, 13),
    Dso("M54", "NGC 6715", "globular", 18.9177, -30.4783, 7.6, 12, 12),
    Dso("M55", "NGC 6809", "globular", 19.6664, -30.9717, 6.3, 19, 19),
    Dso("M56", "NGC 6779", "globular", 19.2767, 30.1833, 8.3, 9, 9),
    Dso("M62", "NGC 6266", "globular", 17.0203, -30.1133, 6.5, 15, 15),
    Dso("M68", "NGC 4590", "globular", 12.6567, -26.7447, 7.8, 11, 11),
    Dso("M69", "NGC 6637", "globular", 18.5231, -32.3481, 7.6, 10, 10),
    Dso("M70", "NGC 6681", "globular", 18.7203, -32.2919, 7.9, 8, 8),
    Dso("M71", "NGC 6838", "globular", 19.8961, 18.78, 8.2, 7, 7),
    Dso("M72", "NGC 6981", "globular", 20.8908, -12.5367, 9.3, 6.6, 6.6),
    Dso("M75", "NGC 6864", "globular", 20.1667, -21.9733, 8.5, 7, 7),
    Dso("M79", "NGC 1904", "globular", 5.4067, -24.5167, 8.5, 6, 6),
    Dso("M80", "NGC 6093", "globular", 16.2839, 22.5167, 7.9, 10, 10),
    Dso("M92", "NGC 6341", "globular", 17.2853, 43.1367, 6.4, 14, 14),
    Dso("M107", "NGC 6171", "globular", 16.8886, -13.05, 7.9, 10, 10),
    Dso(
        "NGC 5139",
        "Omega Centauri",
        "globular",
        13.4464,
        -47.4733,
        3.9,
        36,
        36,
    ),
    Dso("NGC 104", "47 Tucanae", "globular", 0.4014, -72.08, 3.9, 50, 50),
    # ---- Planetary nebulae: small but bright surface ----
    Dso("M27", "Dumbbell Nebula", "pn", 19.9933, 22.72, 7.4, 8, 6),
    Dso("M57", "Ring Nebula", "pn", 18.8931, 33.0292, 8.8, 1.4, 1.0),
    Dso("M76", "Little Dumbbell", "pn", 1.7056, 51.5767, 10.1, 2.7, 1.8),
    Dso("M97", "Owl Nebula", "pn", 11.2464, 55.0167, 9.9, 3.4, 3.0),
    Dso("NGC 6543", "Cat's Eye Nebula", "pn", 17.9758, 66.6333, 8.1, 0.3, 0.3),
    Dso("NGC 7293", "Helix Nebula", "pn", 22.4939, -20.8333, 7.6, 16, 16),
    # ---- Bright / diffuse nebulae and supernova remnants ----
    Dso("M1", "Crab Nebula", "snr", 5.5756, 22.0144, 8.4, 6, 4),
    Dso("M8", "Lagoon Nebula", "bn", 18.0608, -24.3867, 6.0, 90, 40),
    Dso("M16", "Eagle Nebula", "bn", 18.3133, -13.7914, 6.0, 35, 28),
    Dso("M17", "Omega Nebula", "bn", 18.3464, -16.1769, 6.0, 46, 18),
    Dso("M18", "NGC 6613", "open", 18.3333, -17.1333, 7.5, 9, 9),
    Dso("M20", "Trifid Nebula", "bn", 18.04, -23.03, 6.3, 28, 28),
    Dso("M21", "NGC 6531", "open", 18.0733, -22.5, 6.5, 16, 16),
    Dso("M42", "Orion Nebula", "bn", 5.5881, -5.3911, 4.0, 65, 60),
    Dso("M43", "De Mairan's Nebula", "bn", 5.5919, -5.2717, 9.0, 20, 15),
    Dso("M78", "NGC 2068", "bn", 5.7797, 0.0667, 8.3, 8, 6),
    Dso("IC 434", "Horsehead region", "bn", 5.6833, -2.4667, 6.8, 60, 10),
    Dso("NGC 2024", "Flame Nebula", "bn", 5.7208, -1.9, 10.0, 20, 20),
    Dso("NGC 281", "Pacman Nebula", "bn", 0.8736, 56.55, 7.4, 35, 28),
    Dso("NGC 2237", "Rosette Nebula", "bn", 6.53, 4.9667, 4.8, 80, 60),
    Dso("NGC 6888", "Crescent Nebula", "bn", 20.2019, 38.3567, 7.4, 25, 10),
    Dso(
        "NGC 7000",
        "North America Nebula",
        "bn",
        20.9881,
        44.5289,
        4.0,
        120,
        100,
    ),
    Dso("NGC 7635", "Bubble Nebula", "bn", 23.3458, 61.1833, 10.0, 15, 8),
    Dso(
        "NGC 6960",
        "Veil Nebula (West)",
        "snr",
        20.7606,
        30.7183,
        7.0,
        70,
        6,
    ),
    Dso(
        "NGC 6992",
        "Veil Nebula (East)",
        "snr",
        20.755,
        31.7167,
        7.0,
        60,
        8,
    ),
    # ---- Galaxies ----
    Dso("M31", "Andromeda Galaxy", "galaxy", 0.7123, 41.2686, 3.4, 178, 63),
    Dso("M32", "NGC 221", "galaxy", 0.7117, 40.8653, 8.1, 9, 7),
    Dso("M33", "Triangulum Galaxy", "galaxy", 1.5642, 30.6603, 5.7, 62, 44),
    Dso("M49", "NGC 4472", "galaxy", 12.4972, -8.0014, 8.4, 10, 8),
    Dso("M51", "Whirlpool Galaxy", "galaxy", 13.498, 47.1953, 8.4, 18, 12),
    Dso("M58", "NGC 4579", "galaxy", 12.3819, 11.815, 9.7, 6, 5),
    Dso("M59", "NGC 4621", "galaxy", 12.4964, 11.8222, 9.6, 5, 4),
    Dso("M60", "NGC 4649", "galaxy", 12.5214, 11.56, 8.8, 7, 6),
    Dso("M61", "NGC 4303", "galaxy", 12.3653, 4.4733, 9.7, 6, 5),
    Dso("M63", "Sunflower Galaxy", "galaxy", 13.2636, 42.03, 8.6, 13, 7),
    Dso("M64", "Black Eye Galaxy", "galaxy", 12.9455, 21.6817, 8.5, 10, 5),
    Dso("M65", "NGC 3623", "galaxy", 11.3153, 13.0917, 9.3, 10, 3),
    Dso("M66", "NGC 3627", "galaxy", 11.3375, 12.9917, 8.9, 9, 4),
    Dso("M74", "NGC 628", "galaxy", 1.6117, 15.7833, 9.4, 10, 9),
    Dso("M77", "NGC 1068", "galaxy", 2.7114, -0.0133, 8.9, 7, 6),
    Dso("M81", "Bode's Galaxy", "galaxy", 9.9258, 69.0653, 6.9, 21, 14),
    Dso("M82", "Cigar Galaxy", "galaxy", 9.9312, 69.6797, 8.4, 11, 4),
    Dso(
        "M83",
        "Southern Pinwheel",
        "galaxy",
        13.6169,
        -29.8667,
        7.5,
        13,
        11,
    ),
    Dso("M84", "NGC 4374", "galaxy", 12.4178, 12.9517, 9.1, 6, 5),
    Dso("M85", "NGC 4382", "galaxy", 12.4208, 18.1117, 9.1, 7, 5),
    Dso("M86", "NGC 4406", "galaxy", 12.4367, 12.955, 8.9, 7, 6),
    Dso("M87", "Virgo A", "galaxy", 12.5137, 12.3911, 8.6, 7, 7),
    Dso("M88", "NGC 4501", "galaxy", 12.5297, 14.425, 9.6, 7, 4),
    Dso("M89", "NGC 4552", "galaxy", 12.5944, 12.5517, 9.8, 4, 4),
    Dso("M90", "NGC 4569", "galaxy", 12.6139, 13.1633, 9.5, 10, 5),
    Dso("M91", "NGC 4548", "galaxy", 12.5906, 14.4983, 10.2, 5, 4),
    Dso("M94", "NGC 4736", "galaxy", 12.8481, 41.12, 8.2, 11, 9),
    Dso("M95", "NGC 3351", "galaxy", 10.7794, 11.705, 9.7, 7, 3),
    Dso("M96", "NGC 3368", "galaxy", 10.7794, 11.8183, 9.2, 8, 5),
    Dso("M98", "NGC 4192", "galaxy", 12.23, 14.9067, 10.1, 6, 3),
    Dso("M99", "NGC 4254", "galaxy", 12.3136, 14.4167, 9.9, 5, 5),
    Dso("M100", "NGC 4321", "galaxy", 12.3819, 15.8233, 9.3, 7, 6),
    Dso("M101", "Pinwheel Galaxy", "galaxy", 14.0536, 54.3483, 7.9, 29, 27),
    Dso("M102", "Spindle Galaxy", "galaxy", 15.1822, 55.7617, 9.9, 7, 3),
    Dso("M104", "Sombrero Galaxy", "galaxy", 12.6664, -11.6233, 8.0, 9, 4),
    Dso("M105", "NGC 3384", "galaxy", 10.7969, 12.5817, 9.3, 4, 3),
    Dso("M106", "NGC 4258", "galaxy", 12.3167, 47.3039, 8.4, 19, 8),
    Dso("M108", "NGC 3556", "galaxy", 11.1919, 55.6667, 10.0, 8, 2),
    Dso("M109", "NGC 3992", "galaxy", 11.9686, 53.375, 9.8, 8, 5),
    Dso("M110", "NGC 205", "galaxy", 0.675, 41.685, 8.5, 17, 10),
    Dso("NGC 253", "Sculptor Galaxy", "galaxy", 0.7925, -25.2833, 7.1, 28, 13),
    Dso("NGC 2903", "NGC 2903", "galaxy", 9.4206, 21.7833, 8.9, 13, 6),
    Dso("NGC 300", "NGC 300", "galaxy", 0.9142, -37.685, 8.1, 22, 15),
    Dso(
        "NGC 3628",
        "Leo Triplet (Sarah's Galaxy)",
        "galaxy",
        11.3375,
        13.59,
        9.5,
        15,
        4,
    ),
    Dso("NGC 4565", "Needle Galaxy", "galaxy", 12.5853, 25.9908, 9.6, 16, 2),
    Dso("NGC 7331", "NGC 7331", "galaxy", 22.6178, 34.4733, 9.5, 10, 4),
    Dso("NGC 891", "NGC 891", "galaxy", 2.3758, 42.3492, 10.0, 14, 3),
    Dso(
        "NGC 1365",
        "Great Barred Spiral",
        "galaxy",
        3.56,
        -36.14,
        9.7,
        11,
        6,
    ),
    Dso(
        "NGC 5128",
        "Centaurus A",
        "galaxy",
        13.4211,
        -43.0189,
        6.8,
        26,
        20,
    ),
)
