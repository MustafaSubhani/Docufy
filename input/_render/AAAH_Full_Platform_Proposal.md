---
title: AAAH Holding Corp
subtitle: Enterprise AI Platform — On-Premise Proof of Concept
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

Spark Technologies proposes to deploy the **AAAH Enterprise AI Platform** on AAAH Holding Corp's premises — so project data stays inside your infrastructure, with no dependency on an external cloud for day-to-day operation.

The platform brings together eight AI-powered tools in one system: reading and drafting documents, analysing drawings, tracking site progress, and detecting clashes in building models.

This document covers:

- The physical equipment needed to run the platform
- The data and access AAAH Holding Corp needs to provide
- How the system can grow as demand expands

:::callout
### PoC Scope
**1–2 active project sites**, up to **100 users**, and up to **10 concurrent AI users**. Timeline: **6 weeks**.
:::

:::callout
### What this proposal covers
This proposal covers **equipment and data/access requirements only**. Platform licensing, development, deployment services, and project management are quoted separately.
:::

# 02 Platform Modules

## What the Platform Does

| Module | What It Does |
| --- | --- |
| Executive Dashboard | Live summary of project KPIs, progress curves, and activity across modules |
| Drawing Scanner & BOQ | Reads CAD drawings and generates quantity take-offs |
| Document OCR & Parsing | Extracts key information from invoices, contracts, and purchase orders |
| Document Drafting | Drafts contracts, NOCs, and RFIs using AI, ready for review and approval |
| Clash Detection | Identifies conflicts between building models (for example structure vs MEP) |
| Site Monitoring | Connects to existing site cameras to detect safety issues and track progress |
| DMS & AI Assistant | Searchable document library with an AI assistant grounded in your files |
| Progress Monitoring | Tracks planned vs actual progress, updates WBS activities, and flags delays |

# 03 Equipment Required

## Equipment Required

The PoC platform runs on a **single server** installed at your premises — one machine to manage, one maintenance point, and a clear upgrade path.

### The AI Server

| Item | What It Is | Price (AED) |
| --- | --- | --- |
| AI Processing Cards × 2 | NVIDIA L40S — runs AI features across modules. Two cards support the PoC concurrency target | 92,400 |
| Server Machine | Dell PowerEdge R760xa — rack-mounted server housing the platform | 19,800 |
| Memory (RAM) | 256GB working memory for concurrent workloads | 9,900 |
| Fast Internal Storage | 10TB high-speed drives for AI models and active project data | 8,800 |
| **Server Total** | | **AED 130,900** |

### Supporting Equipment

| Item | What It Is | Price (AED) |
| --- | --- | --- |
| Server Cabinet | Standard 42U rack to house and protect the server | 4,400 |
| Network Switch | Connects users and systems on the internal network | 7,150 |
| Document & Video Storage | 50TB Synology NAS for documents, drawings, camera footage, and AI data | 25,300 |
| Firewall | Fortinet FortiGate — network protection for the server | 9,350 |
| Cabling & Setup Materials | Network cables, power strips, and installation materials | 4,400 |
| **Supporting Equipment Total** | | **AED 50,600** |

### Total Equipment Investment

| | Price (AED) |
| --- | --- |
| AI Server | 130,900 |
| Supporting Equipment | 50,600 |
| **Total** | **AED 181,500** |

:::callout
### Pricing notes
Prices are based on UAE distributor rates as of September 2026 and are **exclusive of VAT**. Final pricing is subject to formal vendor quotation. This quotation is valid for **30 days** from the proposal date.
:::

### Built to Grow

Additional AI processing cards can be added later **without replacing the server**. The PoC starts with 2 cards; the chassis can hold up to 4.

| Stage | AI Cards | Users Supported |
| --- | --- | --- |
| PoC (Now) | 2 cards | 100 users, 10 simultaneous AI |
| Phase 2 | 3 cards | ~150 users, 20 simultaneous AI |
| Full Scale | 4 cards | 200+ users, 35+ simultaneous AI |

:::callout
### Future expansion
Adding a card later costs approximately **AED 46,200** and does not require a new server.
:::

# 04 Site Requirements

## Space & Environment at Your Site

| Requirement | Details |
| --- | --- |
| Power | Dedicated 20A power socket (standard single-phase 220V) |
| Cooling | Good cooling / ventilation around the server — standard office or IT-room air conditioning is sufficient |
| Network for Support | Controlled connectivity for remote support and approved updates (no day-to-day cloud dependency for platform operation) |
| Internal Network | Gigabit connection to staff workstations |

# 05 Client Inputs

## What We Need from AAAH Holding Corp

### Project Data

| What We Need | Format | Why We Need It | When |
| --- | --- | --- | --- |
| 3D Building Models | Revit or IFC | Clash detection and progress tracking | Week 1 |
| Project Schedules | Primavera P6 or MS Project | S-Curve and delay calculation | Week 1 |
| Past Project Records | PDF, Excel, or P6 | Helps the AI learn your project patterns | Week 1 |
| Drawings & CAD Files | DWG, DXF, or PDF | Drawing scanner and BOQ | Week 2 |
| Contracts & Documents | PDF or Word | Document reading and AI drafting | Week 2 |
| Invoices & Purchase Orders | PDF | Document parsing | Week 2 |
| Work Breakdown Structure (WBS) | Excel or P6 export | Map activities to the progress dashboard | Week 1 |

### System Access

| What We Need | Details | Why |
| --- | --- | --- |
| Primavera P6 or MS Project | Read-only access or scheduled export | Keep schedules in sync |
| ERP System (SAP or similar) | Read-only API access | Pull purchase order and invoice data |
| Site Camera System | RTSP stream access or camera credentials | Connect cameras to the AI pipeline |
| Company Login System | Active Directory or SSO | Users log in with existing accounts |
| Internal Network | Fixed IP allocated to the server | Users can reach the platform |

### People We Will Coordinate With

| Role | Why |
| --- | --- |
| IT Contact | Server installation and network setup in weeks 1–2 |
| Project Manager | Confirm schedule and WBS data |
| Site Supervisor | Verify progress readings during the PoC |
| Camera System Administrator | Provide camera access credentials |

# 06 Capacity and Options

## Platform Capacity

| | Value |
| --- | --- |
| Total Users | 100 |
| Users Running AI at the Same Time | 10 |
| Project Sites (PoC) | 1–2 |
| Camera Streams Supported | Up to 12 using existing cameras |
| Storage for Documents & Data | 50TB |
| AI Response Time | Target under 8 seconds for most queries at PoC load |

## Drone Integration — Optional

Progress Monitoring can use drone surveys as an optional add-on. Not required for the PoC.

| | Without Drone | With Drone |
| --- | --- | --- |
| How progress is measured | AI reads existing cameras | AI also processes aerial drone images |
| Indicative accuracy | Depends on camera coverage, angles, and labelling quality | Higher when drone imagery and model comparison are available |
| Comparison against building model | Limited / not available from cameras alone | Available — built vs designed comparison |
| Extra hardware needed | None | Drone flights (third-party service, hired separately) |

:::callout
### Drone note
Drone flights can be arranged by AAAH as a third-party service. No server or platform redesign is required to enable this later.
:::

# 07 Delivery Plan

## PoC Delivery Plan — 6 Weeks

| Week | What Happens |
| --- | --- |
| Week 1 | Server delivered and installed. Network connected. Core system set up. |
| Week 2 | Project data loaded — schedules, drawings, documents. Camera feeds connected. |
| Week 3 | Document modules go live — OCR, AI Assistant, Document Drafting. User accounts created. |
| Week 4 | Drawing Scanner, Clash Detection, Site Monitoring, and Progress Monitoring go live on the PoC sites. |
| Week 5 | Testing with real project data. Key staff review and validate. Adjustments made. |
| Week 6 | Final refinements. Leadership demo. PoC sign-off against agreed success criteria. |

:::callout
### Assumptions
Week-by-week delivery assumes timely access to the server room, network, camera streams, schedule/WBS data, and named AAAH contacts. Module depth in the PoC is limited to the agreed 1–2 sites and representative workflows, not a full enterprise rollout of every edge case.
:::

### Suggested PoC Success Criteria

- Platform reachable by named PoC users via AAAH accounts
- At least one end-to-end document workflow demonstrated (ingest → extract/draft → review)
- Progress or site monitoring producing usable dashboard outputs on the selected site(s)
- Clash or drawing workflow demonstrated on sample AAAH files
- Risks, limitations, and Phase-2 recommendations documented at sign-off

# 08 Summary

## Summary

| | |
| --- | --- |
| Type of Deployment | On-Premise — data stays at your site |
| Server | Dell PowerEdge R760xa |
| AI Engine | 2× NVIDIA L40S — expandable to 4× without hardware replacement |
| Total Users | 100 |
| Simultaneous AI Users | 10 |
| Storage | 50TB document/video storage + 10TB high-speed internal |
| PoC Sites | 1–2 |
| Drone | Optional — supported |
| Delivery | 6 Weeks |
| Total Equipment Cost | **AED 181,500** (excl. VAT) |
| Prepared by | Spark Technologies |
| Prepared for | AAAH Holding Corp |
