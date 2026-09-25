---
title: AAAH Holding Corp
subtitle: AI Progress Monitoring — On-Premise Proof of Concept
doc_type: PROPOSAL
prepared_for: AAAH Holding Corp
prepared_by: Spark Technologies
date: September 1, 2026
confidential: true
section_break: true
show_contact: true
footer: Spark Technologies  |  Confidential
---

# 01 Overview

## Overview

Spark Technologies proposes deploying the **AAAH AI Progress Monitoring Module** as a standalone on-premise solution — the most immediately impactful component of the full AAAH platform, and a natural first step before a wider rollout.

The module connects to your **existing site cameras** and uses AI to track construction progress, update activity completion, and flag delays — without manual progress data entry from the site team. The same cameras used for safety monitoring also feed progress tracking.

This document covers:

- The equipment needed to run the module
- The data and access AAAH Holding Corp needs to provide
- The information needed to train the AI for your project types

:::callout
### PoC Scope
**1–2 active project sites**, up to **100 users**, and up to **10 concurrent AI users**. Timeline: **13 weeks (3 months)**.
:::

:::callout
### What this proposal covers
This proposal covers **equipment and data/access requirements only**. Platform licensing, development, deployment services, and project management are quoted separately.
:::

# 02 Capabilities

## What the Module Does

| Feature | Description |
| --- | --- |
| Zone Completion Tracking | AI watches camera feeds and recognises construction milestones — such as a slab pour or completed columns — and marks the zone accordingly |
| Automatic Schedule Updates | Detected milestones update the corresponding activity in the project schedule |
| Planned vs Actual Progress (S-Curve) | Real-time view of actual site progress against the planned schedule |
| Delay Detection & Alerts | Identifies activities that are behind and by how many days |
| Site Map View | Colour-coded site map showing done, in progress, delayed, or not started zones |
| Schedule Performance Metrics | Calculates project health indicators such as SPI and Schedule Variance |
| Drone Integration (Optional) | Drone survey images can be added for higher-detail progress views |

## How It Works

1. Existing site cameras stream into the on-premise AI server
2. The AI reads live feeds and detects progress milestones
3. Safety alerts and progress tracking can share the same camera inputs
4. The Progress Dashboard updates S-Curve, KPIs, and delay alerts

:::callout
### One camera — two uses
The same camera that monitors safety can also feed progress tracking. No additional cameras are required for the PoC, provided existing coverage is adequate for the selected zones.
:::

# 03 Equipment Required

## Equipment Required

### The AI Server

| Item | What It Is | Price (AED) |
| --- | --- | --- |
| AI Processing Card × 1 | NVIDIA L40S — computer vision and AI for the module | 46,200 |
| Server Machine | Dell PowerEdge R760xa — GPU-capable 2U rack server (supports L40S) | 13,200 |
| Memory (RAM) | 128GB for camera streams and AI models | 4,950 |
| Fast Internal Storage | 4TB high-speed drives for the AI model and active project data | 4,400 |
| **Server Total** | | **AED 68,750** |

### Supporting Equipment

| Item | What It Is | Price (AED) |
| --- | --- | --- |
| Server Cabinet | Compact 12U wall-mount rack | 3,300 |
| Network Switch | Connects the server to cameras and user workstations | 5,500 |
| Document & Video Storage | 20TB Synology NAS for recordings, reports, and project data | 13,200 |
| Firewall | Fortinet FortiGate — network protection for the server | 5,500 |
| Cabling & Setup Materials | Network cables, power strips, and installation materials | 3,300 |
| **Supporting Equipment Total** | | **AED 30,800** |

### Total Equipment Investment

| | Price (AED) |
| --- | --- |
| AI Server | 68,750 |
| Supporting Equipment | 30,800 |
| **Total** | **AED 99,550** |

:::callout
### Pricing notes
Prices are based on UAE distributor rates as of September 2026 and are **exclusive of VAT**. Final pricing is subject to formal vendor quotation. This quotation is valid for **30 days** from the proposal date.
:::

### Built to Grow

Starting with one AI card keeps the PoC cost-efficient. As sites or accuracy needs increase, the same server can accept additional cards.

| Stage | AI Cards | Sites Supported |
| --- | --- | --- |
| PoC (Now) | 1 card | 1–2 sites, up to 12 camera streams |
| Phase 2 | 2 cards | 4–6 sites, ~25 camera streams |
| Full Platform | Move to Full Platform hardware | All sites, all AI modules |

:::callout
### Future expansion
Adding a card later costs approximately **AED 46,200** — no new server needed for that step.
:::

# 04 Site Requirements

## Space & Environment at Your Site

| Requirement | Details |
| --- | --- |
| Power | Standard 15A wall socket (220V) — no special power setup |
| Cooling | Good cooling / ventilation around the server — standard office air conditioning is sufficient |
| Network | Same network as site cameras and user computers |
| Camera Network | Server must reach cameras over the internal network |

# 05 Client Inputs

## What We Need from AAAH Holding Corp

### To Run the System (Needed at Go-Live)

| What We Need | Format | Why We Need It | When |
| --- | --- | --- | --- |
| Project Schedule (Baseline) | Primavera P6 or MS Project | Measure actual progress against plan | Week 1 |
| Work Breakdown Structure (WBS) | P6 export or Excel | Link detected milestones to schedule activities | Week 1 |
| Site Zone Layout | Drawing, sketch, or PDF | Map cameras to site zones | Week 2 |
| Building Model (Optional) | Revit or IFC | Improves zone detection accuracy | Week 2 |

### To Train the AI (Important for Accuracy)

The AI comes pre-trained on general construction data. Accuracy on AAAH projects improves when it is fine-tuned with your own examples.

| What We Need | Format | How Much | Why |
| --- | --- | --- | --- |
| Past site photos | JPG or PNG | At least 2,000 photos across project types | Teaches stage recognition on your sites |
| Milestone photos with notes | Labelled photos (e.g. "columns done", "slab poured") | 500+ labelled examples | Defines what "complete" looks like per activity |
| Past completed project records | Excel, P6, or PDF | 3–5 completed projects | Baseline for delay and performance patterns |
| Sample camera recordings | Short clips from site cameras | 2–4 hours per camera angle | Calibrate to your angles and conditions |
| Zone boundaries | Sketch or drawing overlay | One per site | Maps site areas to schedule zones |

### System Access Needed

| What We Need | Details | Why |
| --- | --- | --- |
| Site Camera Access | RTSP links or camera login credentials | AI reads live feeds |
| Project Schedule Access | Read-only P6 / MS Project access or scheduled exports | Keep the dashboard in sync |
| Company Login System | Active Directory or SSO | Users keep existing AAAH accounts |
| Internal Network | Fixed address assigned to the server | Users and cameras can reach the platform |

### People We Will Coordinate With

| Role | Why |
| --- | --- |
| IT Contact | Installation, network setup, and camera connectivity |
| Project Manager | Confirm schedule data and validate progress readings in Weeks 9–11 |
| Site Supervisor | Cross-check AI progress against site conditions in Weeks 9–11 |
| Camera System Administrator | Provide access to existing camera streams |

# 06 Capacity and Options

## Platform Capacity

| | Value |
| --- | --- |
| Total Users | 100 |
| Users Running AI at the Same Time | 10 |
| Project Sites (PoC) | 1–2 |
| Camera Streams Supported | Up to 12 using existing cameras |
| Storage | 20TB for recordings, documents, and reports |
| Schedule Refresh | Near-real-time detection + daily reconciliation with P6 |
| Dashboard Update Frequency | Every 15 minutes |

## Drone Integration — Optional

The module can accept drone survey data later, without server or software redesign.

| | Without Drone | With Drone |
| --- | --- | --- |
| How progress is tracked | AI reads existing cameras | AI also reads aerial drone images |
| Indicative accuracy | Depends on coverage, angles, labelling, and zone mapping | Higher when drone imagery and design comparison are available |
| Comparison against building design | Limited / not available from cameras alone | Built vs designed comparison |
| Extra cost | None | Drone flight service arranged separately by AAAH |

:::callout
### Accuracy note
Camera-only accuracy is **indicative** and depends on camera placement, occlusion, lighting, and the quality of AAAH training data. Weeks 9–11 are used to measure actual accuracy on your sites before any production target is agreed.
:::

# 07 Delivery Plan

## Delivery Plan — 13 Weeks (3 Months)

| Week | What Happens |
| --- | --- |
| Week 1 | Technical discovery with AAAH project controls, IT, and site teams. PoC site and zones selected. |
| Week 2 | Server delivered and installed. Network connected. Camera streams tested and frame capture verified. |
| Week 3 | Baseline schedule and WBS loaded. Site zone map configured and cameras mapped to zones. |
| Weeks 3–4 | Training data ingested and reviewed. Labelling gaps identified and closed with AAAH. |
| Weeks 4–5 | AI fine-tuned on AAAH historical photos and project records (subject to data readiness). |
| Weeks 5–6 | Milestone detection calibrated per activity type and per camera angle. |
| Weeks 6–7 | Schedule ingest and daily reconciliation tested against P6 / MS Project (read-only or scheduled export). |
| Weeks 7–8 | Progress dashboard configured — S-Curve, site map, delay alerts, SPI and SV. User accounts created. |
| Weeks 8–9 | Live progress tracking enabled on the PoC site(s), running in parallel with existing reporting. |
| Weeks 9–11 | Accuracy review — AI readings compared against site conditions. Model adjustments and re-tuning. |
| Weeks 11–12 | Role-based access configured for agreed user groups. User training delivered. |
| Week 13 | Final refinements. User walkthrough. PoC sign-off, demo, and Phase-2 recommendations documented. |

:::callout
### Assumptions
Delivery assumes camera streams are reachable, the baseline schedule and WBS are available by Week 2, and enough labelled historical photos are provided by Week 3 for fine-tuning. If training data is late or incomplete, accuracy validation may slip into Weeks 11–13 without changing the equipment scope.
:::

### Suggested PoC Success Criteria

- Live camera feeds processed for the selected site zone(s)
- Progress dashboard showing planned vs actual for agreed activities
- Delay flags reviewed with the Project Manager / Site Supervisor
- Measured accuracy and limitations documented at sign-off
- Clear recommendation on whether to expand sites or move toward the full platform

# 08 Summary

## Summary

| | |
| --- | --- |
| Type of Deployment | On-Premise — data stays at your site |
| Server | Dell PowerEdge R760xa |
| AI Engine | 1× NVIDIA L40S — expandable without replacing the server |
| Total Users | 100 |
| Simultaneous AI Users | 10 |
| Camera Streams | Up to 12 using existing cameras |
| Storage | 20TB document/video storage + 4TB high-speed internal |
| PoC Sites | 1–2 |
| Drone | Optional — supported, not required |
| Delivery | 13 Weeks (3 months) |
| Total Equipment Cost | **AED 99,550** (excl. VAT) |
| Prepared by | Spark Technologies |
| Prepared for | AAAH Holding Corp |
