# Report specification (page by page)

Every page has these common elements:

- **Slicers:** Date (relative: last 30 days by default), Plant, Product Family.
- **Footer card:** `[Data As Of]`.
- **Theme:** corporate. OEE colour rule: ≥ 85% green, 60–85% amber, < 60% red.

| # | Page | Visuals (fields) | Storage used |
|---|---|---|---|
| 1 | **Executive Overview** | KPI cards: `OEE %` vs `OEE Target`, `Units Produced` with `Units Produced YoY %`, `First Pass Yield %`, `Scrap Cost`. Line chart: `OEE 7D Rolling` by `Date[calendar_date]`. Table from **Exec KPI Monthly** (month, plant, scrap rate, FPY, availability, scrap cost). | Import + DirectQuery |
| 2 | **Plant OEE** | Clustered bars: `Availability %`, `Performance %`, `Quality %`, `OEE %` by `Plant[plant_name]`. Matrix: Plant → `Machine[line_id]` → `Machine[machine_id]` × (OEE %, Downtime Minutes, Units Produced). Waterfall: planned → downtime → speed loss → quality loss. | Import |
| 3 | **Machine Drill-down** (drill-through on `Machine[machine_id]`) | Line: `Units Produced` and `Ideal Units` by day. Column: `Downtime Minutes` by day. Card: `Sensor Anomaly Minutes`. Table of SCD2 history: `Machine[line_id]`, `Machine[status]`, `effective_from/to`, which shows the machine's moves. | Import |
| 4 | **Quality & Defects** | Pareto: defect units by `Defect Type[defect_code]` with cumulative %. Heatmap: `Defects PPM` by `Product[product_family]` × `Date[iso_week]`. Drill-through to inspections (`Fact Quality Inspection`). | Import |
| 5 | **Maintenance** | Cards: `MTTR (min)`, `MTBF (hrs)`, `Open Work Orders`. Bar: `Breakdowns` by machine. Scatter: `MTBF` vs `MTTR` per machine (bottom-right = worst). | Import |
| 6 | **Supplier Quality** | Bar: `Supplier Reject Rate %` by `Supplier[supplier_name]`. Table: material × supplier × delivered/rejected. | Import |
| 7 | **Shopfloor Live** | Tiles per machine from **Shopfloor Status**: `health_rag` conditional colour, `oee`, `open_work_orders`, `anomaly_minutes`, `data_as_of`. **Automatic page refresh: 15 minutes.** | DirectQuery |

Bookmarks: "Last 7 days", "Month to date". Mobile layout: pages 1 and 7.
