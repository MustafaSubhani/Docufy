# Spark AI — Auto-Takeoff Techniques (Internal)

Reference catalog of methods for **automatic room boundary inference** and **quantity extraction** from architectural floor plans. Describes mechanisms and failure modes; does not prescribe a product pipeline.

---

## Scope & outputs

**Input:** floor-plan PDF or raster image.  
**Target output:** closed polygons per enclosed space + area (SF/SY) once scale is calibrated.

Floor plans are not semantically structured — they expose drawing primitives (paths, text, optional OCG layers), not room objects. Auto-takeoff is **inverse graphics**: classify ink as walls vs noise, recover enclosed regions, vectorize boundaries.

```mermaid
flowchart LR
  subgraph inStage [Input]
    PDF[PDF or image]
    SCALE[Sheet scale]
  end
  subgraph inferStage [Inference]
    DET[Region detection]
    VEC[Boundary vectorization]
  end
  subgraph outStage [Output]
    POLY[Room polygons]
    QTY[Areas and perimeters]
  end
  PDF --> DET --> VEC --> POLY
  SCALE --> QTY
  POLY --> QTY
```

---

## Input modality taxonomy

Detection quality depends heavily on how the sheet was exported. Same building, different PDF structure → different viable techniques.

```mermaid
flowchart TB
  ROOT[Floor plan input]

  ROOT --> LC[Layered CAD]
  ROOT --> FV[Flat vector]
  ROOT --> TF[Tagged finish plan]
  ROOT --> DH[Dimension-heavy sheet]
  ROOT --> RS[Raster or scan]
  ROOT --> RR[Residential-style raster]

  LC --> LC1[OCG wall layers]
  LC --> LC2[Door and window layers]
  LC --> LC3[Clean structural mask]

  FV --> FV1[Merged linework stack]
  FV --> FV2[Grid and dims as barriers]
  FV --> FV3[Title block ink]

  TF --> TF1[Room number text layer]
  TF --> TF2[Label seeding viable]

  DH --> DH1[Repeated numeric strings]
  DH --> DH2[False label seeds]

  RS --> RS1[No vector paths]
  RS --> RS2[Threshold or ML pipeline]

  RR --> RR1[Colored room fills]
  RR --> RR2[Public ML pretrain domain]
```

| Modality | Geometry | Layer metadata | Typical barrier noise |
|----------|----------|----------------|------------------------|
| **Layered CAD (OCG)** | Vector | Walls, openings named | Low — non-structural layers excludable |
| **Flat vector export** | Vector | None or ignored | High — text, grid, hatch, legends |
| **Tagged finish plan** | Vector + text | Varies | Medium — depends on tag vs dimension disambiguation |
| **Dimension-heavy sheet** | Vector + repeated numerics | Varies | High — numeric tokens mimic room IDs |
| **Scanned / image PDF** | Raster only | N/A | Medium — resolution & threshold dependent |
| **Residential-style image** | Raster | N/A | Low for ML pretrain; unlike commercial CAD |

**Layered CAD export** — PDF retains OCG groups (walls, doors, windows, annotations). A layer filter can build a mask from structural linework only.

**Flat vector export** — all primitives share one stack. Walls, dimensions, grid, hatching, and title-block graphics contribute equally to a naive mask.

**Tagged finish plans** — room identifiers in the text layer provide seed coordinates for flood-based region growing; fails when dimension strings match room-number patterns.

**Dimension-heavy sheets** — repeated numeric strings (finish codes, chain dimensions) trigger false seeds if label heuristics are too loose.

**Raster-only inputs** — require render → pixel pipeline; vector flood is unavailable.

**Residential-style imagery** — colored fills and simple topology; aligns with public datasets (CubiCasa5K) but diverges from commercial CAD linework.

---

## Approach families

Three independent families. Production systems often chain one primary detector with shared post-processing.

```mermaid
flowchart TB
  PLAN[Floor plan] --> GATE{Extractable vector paths?}

  subgraph vecPath [Vector inference]
    V1[Segment extraction]
    V2[Optional OCG filter]
    V3[Wall mask rasterization]
    V4[Region growing]
    V5[Contour trace and snap]
    V1 --> V2 --> V3 --> V4 --> V5
  end

  subgraph rasPath [Raster inference]
    R1[Page render and crop]
    R2{Method}
    R3[Classical CV]
    R4[Neural segmentation]
    R5[Graph or sequence model]
    R1 --> R2
    R2 --> R3
    R2 --> R4
    R2 --> R5
  end

  GATE -->|Yes| V1
  GATE -->|No| R1
  V5 --> POST[Post-inference filtering]
  R3 --> POST
  R4 --> POST
  R5 --> POST
  POST --> OUT[Room polygons and QA overlay]
```

| Family | Data used | Training | Latency | CAD fit (general) |
|--------|-----------|----------|---------|-------------------|
| Vector geometry | PDF operators | None | Low | Strong on layered CAD; noisy on flat export |
| Classical CV | Rendered bitmap | None | Low–medium | Weak on dense partitions |
| Neural segmentation | Cropped bitmap | Pretrain + optional fine-tune | Medium–high | Domain-dependent |
| Graph / sequence | Cropped bitmap | Pretrain | High | Research-grade; vector topology output |

---

## Vector-based room inference

Operates on extracted line segments. Core pattern: **barrier mask → seed → flood fill → polygon trace**.

```mermaid
flowchart TD
  A[PDF operator stream] --> B{OCG layers present?}
  B -->|Yes| C[Filter walls and openings]
  B -->|No| D[All constructPath ops]
  C --> E[Segment list]
  D --> E
  E --> F[Rasterize to wall mask]
  F --> G{Seed strategy}
  G --> H[Text label seeds]
  G --> I[Void centroid seeds]
  H --> J[Flood fill and door-gap seal]
  I --> J
  J --> K{Flood status}
  K -->|Enclosed| L[Trace boundary]
  K -->|Leak or tiny| M[Discard]
  L --> N[Snap to vector endpoints]
  N --> O[Closed polygon]
```

| Component | Mechanism | Failure mode |
|-----------|-----------|--------------|
| **Wall mask** | Rasterize segments; ink = barrier, interior = fillable | Line weight & scale affect gap width |
| **OCG layer filter** | Drop non-structural layers before mask build | No-op when layers absent |
| **Flood fill** | BFS/DFS from seed until barrier contact | Leaks through door gaps, mask breaks |
| **Label seeds** | Text position → seed point | Dimension / finish codes as false tags |
| **Void seeds** | Centroid per enclosed open component | Micro-void explosion without area gates |
| **Gap sealing** | Morphological bridge on mask (door-width) | Over-seal merges adjacent rooms |
| **Hatch transparency** | Classify hatch families; secondary flood pass | Residual hatch blocks fill |
| **Trace + snap** | Pixel contour → polygon; snap to snap grid | Jagged trace if snap sparse |
| **Polygonize** | Planar graph faces from wall edges | Requires clean junction topology |

```mermaid
sequenceDiagram
  participant PDF
  participant Filter as OCG filter
  participant Mask as Wall mask
  participant Seed
  participant Flood
  participant Trace

  PDF->>Filter: constructPath ops
  Filter->>Mask: structural segments only
  Mask->>Flood: barrier bitmap
  Seed->>Flood: seed coordinates
  Flood-->>Trace: enclosed pixel region
  Trace-->>PDF: snap to vector endpoints
  Note over Trace: Closed ring in image pixels
```

---

## Raster CV pipelines

No learned weights. Page → bitmap → binary or gradient map → region extraction.

```mermaid
flowchart LR
  subgraph preStage [Preprocess]
    R[Render 1-2K px]
    C[Crop viewport]
    G[Grayscale]
  end
  subgraph binStage [Binarize]
    T[Adaptive threshold]
    M[Morph close and dilate]
  end
  subgraph segStage [Segment]
    CC[Connected components]
    WS[Watershed on Sobel gradient]
    CT[Contour to polygon]
  end
  R --> C --> G --> T --> M
  M --> CC --> CT
  M --> WS --> CT
```

| Operation | Role | CAD caveat |
|-----------|------|------------|
| **Threshold** | Ink vs paper separation | Thin walls vanish at low DPI |
| **Morphological close** | Bridge wall gaps | Merges nearby parallel lines |
| **Connected components** | Label enclosed blobs | Misses rooms separated by single-pixel breaks |
| **Watershed (gradient)** | Boundaries along edge ridges | More robust than binary alone on gappy walls |
| **Viewport crop** | Remove title block / margins | Required before downscale |

Dense commercial CAD: threshold + CC often **under-segments** — interior partitions lost at render resolution.

---

## Neural segmentation

Per-pixel classification or multi-head output (rooms, walls, icons, junction heatmaps). Requires crop to model input size (~512 px) on large sheets.

```mermaid
flowchart TB
  subgraph preML [Preprocess]
    IMG[Sheet render]
    CROP[Plan footprint crop]
    NORM[Normalize and resize]
  end
  subgraph modelML [Model]
    ENC[Encoder]
    DEC[Decoder multi-head]
    OUT[Logits rooms walls heatmaps]
  end
  subgraph postML [Postprocess]
    SOFT[Softmax or argmax]
    POLY[Contour or polygonize]
    TTA[Optional rotation TTA]
  end
  IMG --> CROP --> NORM --> ENC --> DEC --> OUT
  OUT --> TTA --> SOFT --> POLY
```

| Method | Architecture | Output heads | Notes |
|--------|--------------|--------------|-------|
| **Semantic segmentation** | U-Net, SegFormer, FCN | Per-pixel class | Simplest decode path |
| **Multi-task (CubiCasa-style)** | Stacked hourglass | Heatmaps + room + icon channels | Heuristic polygonize step |
| **Test-time augmentation** | N rotations, mean logits | Same | Variable benefit on CAD |
| **Fine-tuning** | Transfer from public checkpoint | Same | Needs in-house labeled volume |
| **Pseudo-labeling** | Teacher = vector pipeline masks | Training PNG masks | Bootstraps flat-export labels |

| Pretrained family | Training domain | Untuned behaviour on commercial CAD |
|-------------------|-----------------|-------------------------------------|
| CubiCasa hourglass | ~5k residential, colored | Coarse blobs, not per-room |
| SegFormer floorplan | Simplified plan images | Near-zero usable regions |
| Light U-Net (4-class) | Generic wall/door seg | Collapse to background |
| MitUNet (transformer + U-Net) | Thin-wall focus | Research; boundary accuracy |

---

## Structural prediction (graph & sequence)

Predicts topology or ordered geometry rather than dense pixels — closer to CAD-native output.

```mermaid
flowchart LR
  IMG[Cropped plan image] --> NET[Transformer or GNN]
  NET --> G[Wall junction graph]
  NET --> S[Polygon vertex sequence]
  G --> F[Face extraction to rooms]
  S --> P[Closed rings]
  G --> V[Vector CAD export]
```

| Paradigm | Representation | Typical use |
|----------|----------------|-------------|
| **Wall junction graph** | Nodes = corners; edges = wall segments | Raster-to-vector, 3D extrusion prep |
| **Polygon sequences** | Autoregressive corner lists | Direct room boundary decode |
| **Heatmap → polygon** | Keypoint detection + rules | CubiCasa post-process standard |

---

## Post-inference filtering

Shared cleanup stage regardless of detector family.

```mermaid
flowchart TD
  RAW[Raw region candidates] --> A[Min and max area gate]
  A --> B[Viewport and margin exclusion]
  B --> C[Polygon deduplication]
  C --> D[Nested wrapper suppression]
  D --> E[Label frequency filter]
  E --> F[NMS on ML overlaps]
  F --> G{Scale set?}
  G -->|No| H[Pixel areas only]
  G -->|Yes| I[SF and SY quantities]
  H --> J[Optional human review]
  I --> J
  J --> K[Committed shapes]
```

| Filter | Removes |
|--------|---------|
| Min area | Slivers, text bubbles, symbol boxes |
| Max area (% sheet) | Courtyard floods, exterior shells |
| Viewport clip | Title block, legend band detections |
| Dedup (ring hash) | Duplicate floods from twin seeds |
| Wrapper suppression | Parent polygon engulfing many children |
| Token frequency | Dimension strings seeded as room IDs |
| NMS | Overlapping ML masks |
| Scale gate | Premature quantity commit |
| Review gate | Low-confidence / agent-proposed regions |

---

## Training & label generation

Methods for producing supervised signal when manual masks are scarce.

```mermaid
flowchart LR
  subgraph srcStage [Sources]
    V[Vector auto-masks]
    M[Manual traces]
  end
  subgraph lblStage [Labels]
    PL[Pseudo-label PNG]
    GT[Ground-truth polygons]
  end
  subgraph trainStage [Training]
    FT[Fine-tune checkpoint]
    EVAL[Hold-out eval]
  end
  V --> PL
  M --> GT
  PL --> FT
  GT --> FT
  FT --> EVAL
```

| Technique | Description |
|-----------|-------------|
| **Pseudo-labeling** | Best automatic pipeline → binary room mask for training |
| **Teacher from layered CAD** | Clean OCG masks supervise flat-export fine-tune |
| **Active learning** | Label sheets with highest model error |
| **Public pretrain** | CubiCasa5K, Structured3D, Raster2Graph, WAFFLE |
| **Augmentation** | Crop, rotation, line-thickness jitter |

---

## Empirical notes

Observations from internal evaluation runs — descriptive only.

| Configuration | Observed behaviour |
|---------------|-------------------|
| Layered CAD · void seeds · no area filter | Hundreds of spurious regions |
| Layered CAD · OCG wall filter · void seeds | Order-of-magnitude reduction; plausible region count |
| Layered CAD · unfiltered label seeds | Speckle from dimension text |
| Flat vector · off-the-shelf CubiCasa / SegFormer | Zero to few blobby regions |
| Flat vector · fine-tune ~25 pseudo-labeled sheets | Still low recall |
| Flat vector · filtered vector + margin crop | Moderate recall; footprint visible in QA |
| Flat vector · void seeds · no filters | Thousands of regions |
| ML · full sheet resized to 512 px | Structural detail lost |

---

## References

| Topic | Link |
|-------|------|
| CubiCasa5K | [arXiv:1904.01920](https://arxiv.org/abs/1904.01920) |
| Raster-to-Graph | [project page](https://wutomwu.github.io/publications/2024-Raster2Graph/) |
| Raster2Seq | [arXiv:2602.09016](https://arxiv.org/html/2602.09016v2) |
| MitUNet | [arXiv:2512.02413](https://arxiv.org/html/2512.02413v3) |
| Industry CV + ML takeoff | [Kreo](https://www.kreo.net/news-2d-takeoff/floor-plan-recognition-technologies) |
| Geometry / vision separation | [planlens](https://pypi.org/project/planlens/) |
| Gradient watershed segmentation | [spatial-analysis](https://github.com/adityonugrohoid/spatial-analysis) |
| R2G + segmentation stack | [Neural Floorplan](http://daegeunkim.com/neural_floorplan.html) |

---

*Last updated: 2026-03-26*
