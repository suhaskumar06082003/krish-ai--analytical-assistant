-- Dummy Hangyo "Silver" schema for local testing of the MCP server.
-- Rebuilds the HangyoDev database from scratch on every run (idempotent).
-- Data is deterministic: CHECKSUM-based pseudo-random values, so reruns give identical numbers.

SET NOCOUNT ON;
GO
IF DB_ID('HangyoDev') IS NOT NULL
BEGIN
    ALTER DATABASE HangyoDev SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE HangyoDev;
END
GO
CREATE DATABASE HangyoDev;
GO
USE HangyoDev;
GO
CREATE SCHEMA Silver;
GO
-- A non-Silver schema, to prove the server refuses to touch it.
CREATE SCHEMA Bronze;
GO
CREATE TABLE Bronze.Raw_Secrets (Id int, Secret nvarchar(50));
INSERT Bronze.Raw_Secrets VALUES (1, N'should never be readable via MCP');
GO

SET NOCOUNT ON;

-- Helper number tables
SELECT TOP (20000) CAST(ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS int) AS n
INTO #n
FROM sys.all_objects a CROSS JOIN sys.all_objects b;
SELECT n INTO #k FROM #n WHERE n <= 6;

------------------------------------------------------------------------------
-- Masters
------------------------------------------------------------------------------
CREATE TABLE Silver.Plant_Master (
    Plant_Code varchar(10) NOT NULL PRIMARY KEY,
    Plant_Name nvarchar(100) NOT NULL,
    City nvarchar(50) NOT NULL
);
INSERT Silver.Plant_Master VALUES
    ('P100', N'Hubballi Plant', N'Hubballi'),
    ('P200', N'Bengaluru Plant', N'Bengaluru');

CREATE TABLE Silver.Product_Master (
    Product_ID varchar(20) NOT NULL PRIMARY KEY,
    Product_Name nvarchar(100) NOT NULL,
    Main_Category nvarchar(50) NOT NULL,
    Sub_Category nvarchar(50) NOT NULL,
    Flavour nvarchar(50) NOT NULL,
    Units int NOT NULL,
    ML int NOT NULL,
    MRP decimal(10, 2) NOT NULL,
    MRP_Per_Unit decimal(10, 2) NOT NULL
);
INSERT Silver.Product_Master VALUES
    ('SKU001', N'Vanilla Cup 100ml',          N'Cups',         N'Classic', N'Vanilla',        24, 100,  480,  20),
    ('SKU002', N'Chocolate Cup 100ml',        N'Cups',         N'Classic', N'Chocolate',      24, 100,  600,  25),
    ('SKU003', N'Strawberry Cup 100ml',       N'Cups',         N'Classic', N'Strawberry',     24, 100,  600,  25),
    ('SKU004', N'Butterscotch Cone 120ml',    N'Cones',        N'Premium', N'Butterscotch',   24, 120,  960,  40),
    ('SKU005', N'Chocolate Cone 120ml',       N'Cones',        N'Premium', N'Chocolate',      24, 120,  960,  40),
    ('SKU006', N'Mango Stick 60ml',           N'Sticks',       N'Fruit',   N'Mango',          40,  60,  400,  10),
    ('SKU007', N'Orange Bar 60ml',            N'Sticks',       N'Fruit',   N'Orange',         40,  60,  400,  10),
    ('SKU008', N'Kulfi Malai 70ml',           N'Sticks',       N'Kulfi',   N'Malai',          30,  70,  750,  25),
    ('SKU009', N'Kesar Pista Kulfi 70ml',     N'Sticks',       N'Kulfi',   N'Kesar Pista',    30,  70,  900,  30),
    ('SKU010', N'Vanilla Family Pack 1L',     N'Family Packs', N'Classic', N'Vanilla',         6, 1000, 900, 150),
    ('SKU011', N'Chocolate Family Pack 1L',   N'Family Packs', N'Classic', N'Chocolate',       6, 1000, 1080, 180),
    ('SKU012', N'Tender Coconut Tub 500ml',   N'Tubs',         N'Natural', N'Tender Coconut',  8, 500, 1600, 200),
    ('SKU013', N'Sitaphal Tub 500ml',         N'Tubs',         N'Natural', N'Sitaphal',        8, 500, 1760, 220),
    ('SKU014', N'Choco Bar 70ml',             N'Sticks',       N'Classic', N'Chocolate',      40,  70,  800,  20),
    ('SKU015', N'Cassata Slice 90ml',         N'Desserts',     N'Premium', N'Cassata',        20,  90, 1000,  50),
    ('SKU016', N'Sundae Cup 150ml',           N'Cups',         N'Premium', N'Mixed Fruit',    24, 150, 1440,  60);

CREATE TABLE Silver.Distributor_Master (
    Distributor_Code varchar(10) NOT NULL PRIMARY KEY,
    Distributor_Name nvarchar(100) NOT NULL,
    Distributor_Type varchar(30) NOT NULL,
    Hub nvarchar(50) NOT NULL,
    City nvarchar(50) NOT NULL,
    Is_Active bit NOT NULL
);
INSERT Silver.Distributor_Master
SELECT
    CONCAT('D', RIGHT(CONCAT('000', n), 3)),
    CONCAT(CHOOSE(n, N'Hubballi', N'Dharwad', N'Belagavi', N'Vijayapura', N'Kalaburagi',
                     N'Bengaluru', N'Mysuru', N'Mangaluru', N'Tumakuru', N'Shivamogga'), N' Cold Agencies'),
    CASE WHEN n IN (1, 6) THEN 'Super_Stockist' ELSE 'Direct' END,
    CASE WHEN n <= 5 THEN N'North Karnataka' ELSE N'South Karnataka' END,
    CHOOSE(n, N'Hubballi', N'Dharwad', N'Belagavi', N'Vijayapura', N'Kalaburagi',
              N'Bengaluru', N'Mysuru', N'Mangaluru', N'Tumakuru', N'Shivamogga'),
    CASE WHEN n = 9 THEN 0 ELSE 1 END
FROM #n WHERE n <= 10;

-- 200 retailers: R0001-R0100 North Karnataka, R0101-R0200 South Karnataka
CREATE TABLE Silver.Retailer_Master (
    Retailer_Code varchar(10) NOT NULL PRIMARY KEY,
    Retailer_Name nvarchar(100) NOT NULL,
    Retailer_Type nvarchar(30) NOT NULL,
    Hub nvarchar(50) NOT NULL,
    Distributor_Code varchar(10) NOT NULL,
    Is_Active bit NOT NULL
);
INSERT Silver.Retailer_Master
SELECT
    CONCAT('R', RIGHT(CONCAT('0000', n), 4)),
    CONCAT(CHOOSE(n % 8 + 1, N'Sri Sai', N'Ganesh', N'Lakshmi', N'Annapoorna', N'New Bharath', N'Royal', N'Sagar', N'Om Shakti'),
           N' ', CHOOSE(n % 4 + 1, N'Stores', N'Bakery', N'Cool Point', N'Super Market'), N' #', n),
    CHOOSE(n % 4 + 1, N'Kirana', N'Bakery', N'Parlour', N'Modern Trade'),
    CASE WHEN n <= 100 THEN N'North Karnataka' ELSE N'South Karnataka' END,
    CONCAT('D', RIGHT(CONCAT('000', CASE WHEN n <= 100 THEN n % 5 + 1 ELSE n % 5 + 6 END), 3)),
    CASE WHEN n % 17 = 0 THEN 0 ELSE 1 END
FROM #n WHERE n <= 200;
CREATE INDEX IX_Retailer_Master_Hub ON Silver.Retailer_Master (Hub);

-- Salesmen: per hub 1 ZSM, 1 RSM, 1 ASM, 1 SSO, 2 SO, 4 PSR. Codes S1xx = North, S2xx = South.
CREATE TABLE Silver.Salesman_Master (
    Salesman_Code varchar(10) NOT NULL PRIMARY KEY,
    Name nvarchar(100) NOT NULL,
    MobilePhone varchar(20) NOT NULL,
    Email nvarchar(100) NOT NULL,
    Role varchar(10) NOT NULL
);
INSERT Silver.Salesman_Master VALUES
    ('S101', N'Ravi Patil',      '9800000101', N'ravi.patil@example.com',      'ZSM'),
    ('S102', N'Anil Kulkarni',   '9800000102', N'anil.kulkarni@example.com',   'RSM'),
    ('S103', N'Suresh Hegde',    '9800000103', N'suresh.hegde@example.com',    'ASM'),
    ('S104', N'Prakash Joshi',   '9800000104', N'prakash.joshi@example.com',   'SSO'),
    ('S105', N'Mahesh Desai',    '9800000105', N'mahesh.desai@example.com',    'SO'),
    ('S106', N'Vinay Naik',      '9800000106', N'vinay.naik@example.com',      'SO'),
    ('S107', N'Kiran Shetty',    '9800000107', N'kiran.shetty@example.com',    'PSR'),
    ('S108', N'Manju Gowda',     '9800000108', N'manju.gowda@example.com',     'PSR'),
    ('S109', N'Santosh Bhat',    '9800000109', N'santosh.bhat@example.com',    'PSR'),
    ('S110', N'Raju Hiremath',   '9800000110', N'raju.hiremath@example.com',   'PSR'),
    ('S201', N'Lakshmi Rao',     '9800000201', N'lakshmi.rao@example.com',     'ZSM'),
    ('S202', N'Deepak Murthy',   '9800000202', N'deepak.murthy@example.com',   'RSM'),
    ('S203', N'Naveen Reddy',    '9800000203', N'naveen.reddy@example.com',    'ASM'),
    ('S204', N'Harish Kumar',    '9800000204', N'harish.kumar@example.com',    'SSO'),
    ('S205', N'Shilpa Iyer',     '9800000205', N'shilpa.iyer@example.com',     'SO'),
    ('S206', N'Arjun Prasad',    '9800000206', N'arjun.prasad@example.com',    'SO'),
    ('S207', N'Chetan Gowda',    '9800000207', N'chetan.gowda@example.com',    'PSR'),
    ('S208', N'Pooja Nair',      '9800000208', N'pooja.nair@example.com',      'PSR'),
    ('S209', N'Rahul Shenoy',    '9800000209', N'rahul.shenoy@example.com',    'PSR'),
    ('S210', N'Girish Acharya',  '9800000210', N'girish.acharya@example.com',  'PSR');

CREATE TABLE Silver.Salesman_Mapping (
    Mapping_Id int IDENTITY(1, 1) NOT NULL PRIMARY KEY,
    ZSM_Code varchar(10) NULL,
    RSM_Code varchar(10) NULL,
    ASM_Code varchar(10) NULL,
    SSO_Code varchar(10) NULL,
    SO_Code varchar(10) NULL,
    PSR_Code varchar(10) NULL,
    From_date date NOT NULL,
    To_date date NULL
);
INSERT Silver.Salesman_Mapping (ZSM_Code, RSM_Code, ASM_Code, SSO_Code, SO_Code, PSR_Code, From_date, To_date) VALUES
    -- historical: Kiran (S107) used to report to Vinay (S106) in 2024
    ('S101', 'S102', 'S103', 'S104', 'S106', 'S107', '2024-04-01', '2024-12-31'),
    ('S101', 'S102', 'S103', 'S104', 'S105', 'S107', '2025-01-01', NULL),
    ('S101', 'S102', 'S103', 'S104', 'S105', 'S108', '2025-01-01', NULL),
    ('S101', 'S102', 'S103', 'S104', 'S106', 'S109', '2025-01-01', NULL),
    ('S101', 'S102', 'S103', 'S104', 'S106', 'S110', '2025-01-01', NULL),
    ('S201', 'S202', 'S203', 'S204', 'S205', 'S207', '2025-01-01', NULL),
    ('S201', 'S202', 'S203', 'S204', 'S205', 'S208', '2025-01-01', NULL),
    ('S201', 'S202', 'S203', 'S204', 'S206', 'S209', '2025-01-01', NULL),
    ('S201', 'S202', 'S203', 'S204', 'S206', 'S210', '2025-01-01', NULL);

-- Each retailer maps to its LOWEST-level salesman: usually a PSR, every 10th retailer only has an SO.
CREATE TABLE Silver.Salesman_Retailer_Mapping (
    Salesman_Code varchar(10) NOT NULL,
    Retailer_Code varchar(10) NOT NULL,
    From_date date NOT NULL,
    To_date date NULL
);
INSERT Silver.Salesman_Retailer_Mapping
SELECT
    CONCAT('S', CASE WHEN n <= 100 THEN 1 ELSE 2 END,
           CASE WHEN n % 10 = 0 THEN CASE WHEN n % 4 < 2 THEN '05' ELSE '06' END
                ELSE RIGHT(CONCAT('0', 7 + n % 4), 2) END),
    CONCAT('R', RIGHT(CONCAT('0000', n), 4)),
    '2025-01-01',
    NULL
FROM #n WHERE n <= 200
UNION ALL
-- some historical assignments that ended in 2024
SELECT
    CONCAT('S', CASE WHEN n <= 100 THEN 1 ELSE 2 END, RIGHT(CONCAT('0', 7 + (n + 1) % 4), 2)),
    CONCAT('R', RIGHT(CONCAT('0000', n), 4)),
    '2024-06-01',
    '2024-12-31'
FROM #n WHERE n <= 200 AND n % 25 = 1;
CREATE INDEX IX_SRM_Salesman ON Silver.Salesman_Retailer_Mapping (Salesman_Code);
CREATE INDEX IX_SRM_Retailer ON Silver.Salesman_Retailer_Mapping (Retailer_Code);

-- One beat per PSR
CREATE TABLE Silver.Beat_Master (
    Beat_Code varchar(10) NOT NULL PRIMARY KEY,
    Beat_Name nvarchar(100) NOT NULL
);
INSERT Silver.Beat_Master VALUES
    ('B101', N'Hubballi Old Town'), ('B102', N'Dharwad University Road'),
    ('B103', N'Belagavi Camp'),     ('B104', N'Vijayapura Station Road'),
    ('B201', N'Bengaluru Jayanagar'), ('B202', N'Mysuru Palace Road'),
    ('B203', N'Mangaluru Hampankatta'), ('B204', N'Tumakuru BH Road');

CREATE TABLE Silver.Beat_Salesman_Mapping (
    Beat_Code varchar(10) NOT NULL,
    Salesman_Code varchar(10) NOT NULL
);
INSERT Silver.Beat_Salesman_Mapping VALUES
    ('B101', 'S107'), ('B102', 'S108'), ('B103', 'S109'), ('B104', 'S110'),
    ('B201', 'S207'), ('B202', 'S208'), ('B203', 'S209'), ('B204', 'S210');

CREATE TABLE Silver.Beat_Retailer_Mapping (
    Beat_Code varchar(10) NOT NULL,
    Retailer_Code varchar(10) NOT NULL
);
INSERT Silver.Beat_Retailer_Mapping
SELECT CONCAT('B', CASE WHEN n <= 100 THEN 1 ELSE 2 END, '0', n % 4 + 1),
       CONCAT('R', RIGHT(CONCAT('0000', n), 4))
FROM #n WHERE n <= 200;

------------------------------------------------------------------------------
-- Primary sales (company -> distributor): 3,000 invoices, Jan 2025 - Oct 2026
------------------------------------------------------------------------------
SELECT
    n AS inv,
    DATEADD(day, ABS(CHECKSUM(CONCAT('pd', n))) % 648, CAST('2025-01-01' AS date)) AS d,
    ABS(CHECKSUM(CONCAT('pdist', n))) % 10 + 1 AS dist,
    CASE WHEN n % 20 = 0 THEN 'Returns' ELSE 'Sales' END AS tt
INTO #ph
FROM #n WHERE n <= 3000;

SELECT h.inv, k.n AS line_no,
       ABS(CHECKSUM(CONCAT('pp', h.inv, '-', k.n))) % 16 + 1 AS prod,
       5 + ABS(CHECKSUM(CONCAT('pq', h.inv, '-', k.n))) % 40 AS qty
INTO #pl
FROM #ph h
JOIN #k k ON k.n <= 1 + ABS(CHECKSUM(CONCAT('pk', h.inv))) % 4;

CREATE TABLE Silver.Primary_Invoice_Line_Items (
    Invoice_Id int NOT NULL,
    Invoice_Number varchar(20) NOT NULL,
    Line_No int NOT NULL,
    Date date NOT NULL,
    Distributor_Code varchar(10) NOT NULL,
    Plant_Code varchar(10) NOT NULL,
    Transaction_Type varchar(10) NOT NULL,
    Product_Code varchar(20) NOT NULL,
    Quantity int NOT NULL,
    Amount_Excl_Tax decimal(18, 2) NOT NULL,
    Amount_Incl_Tax decimal(18, 2) NOT NULL
);
INSERT Silver.Primary_Invoice_Line_Items
SELECT
    h.inv,
    CONCAT('PINV-', RIGHT(CONCAT('000000', h.inv), 6)),
    l.line_no,
    h.d,
    CONCAT('D', RIGHT(CONCAT('000', h.dist), 3)),
    CASE WHEN h.dist <= 5 THEN 'P100' ELSE 'P200' END,
    h.tt,
    p.Product_ID,
    x.sgn * x.q,
    x.sgn * CAST(x.q * p.MRP * 0.70 AS decimal(18, 2)),
    x.sgn * CAST(x.q * p.MRP * 0.70 * 1.18 AS decimal(18, 2))
FROM #ph h
JOIN #pl l ON l.inv = h.inv
JOIN Silver.Product_Master p ON p.Product_ID = CONCAT('SKU', RIGHT(CONCAT('000', l.prod), 3))
CROSS APPLY (
    SELECT
        CASE WHEN h.tt = 'Returns' THEN -1 ELSE 1 END AS sgn,
        CASE WHEN h.tt = 'Returns' THEN l.qty / 5 + 1
             ELSE CAST(l.qty * CASE WHEN MONTH(h.d) IN (3, 4, 5) THEN 2.0
                                    WHEN MONTH(h.d) IN (7, 8) THEN 0.6 ELSE 1.0 END AS int)
        END AS q
) x;

CREATE TABLE Silver.Primary_Invoices (
    Invoice_Id int NOT NULL,
    Invoice_Number varchar(20) NOT NULL,
    Order_Number varchar(20) NOT NULL,
    Date date NOT NULL,
    Creation_Date datetime2(0) NOT NULL,
    Distributor_Code varchar(10) NOT NULL,
    Billing_Distributor_Code varchar(10) NOT NULL,
    Plant_Code varchar(10) NOT NULL,
    Transaction_Type varchar(10) NOT NULL,
    Quantity int NOT NULL,
    Amount_Excl_Tax decimal(18, 2) NOT NULL,
    Amount_Incl_Tax decimal(18, 2) NOT NULL
);
INSERT Silver.Primary_Invoices
SELECT
    Invoice_Id, Invoice_Number,
    CONCAT('PORD-', RIGHT(CONCAT('000000', Invoice_Id), 6)),
    Date,
    DATEADD(hour, 30, CAST(Date AS datetime2(0))),  -- record created ~a day later; analysis must ignore it
    Distributor_Code,
    CASE WHEN Invoice_Id % 9 = 0 THEN CASE WHEN Plant_Code = 'P100' THEN 'D001' ELSE 'D006' END
         ELSE Distributor_Code END,
    Plant_Code, Transaction_Type,
    SUM(Quantity), SUM(Amount_Excl_Tax), SUM(Amount_Incl_Tax)
FROM Silver.Primary_Invoice_Line_Items
GROUP BY Invoice_Id, Invoice_Number, Date, Distributor_Code, Plant_Code, Transaction_Type;

------------------------------------------------------------------------------
-- Secondary sales (distributor -> retailer): 15,000 invoices, ~52k line items
------------------------------------------------------------------------------
SELECT
    n AS inv,
    DATEADD(day, ABS(CHECKSUM(CONCAT('sd', n))) % 648, CAST('2025-01-01' AS date)) AS d,
    ABS(CHECKSUM(CONCAT('sr', n))) % 200 + 1 AS r
INTO #sh
FROM #n WHERE n <= 15000;

CREATE TABLE Silver.Secondary_Invoice_Line_Items (
    Invoice_Id int NOT NULL,
    Invoice_Number varchar(20) NOT NULL,
    Line_No int NOT NULL,
    Invoice_Date date NOT NULL,
    Distributor_Code varchar(10) NOT NULL,
    Retailer_Code varchar(10) NOT NULL,
    Product_Code varchar(20) NOT NULL,
    Quantity int NOT NULL,
    Amount_Excl_Tax decimal(18, 2) NOT NULL,
    TOD decimal(18, 2) NOT NULL,
    Amount_Incl_Tax decimal(18, 2) NOT NULL
);
INSERT Silver.Secondary_Invoice_Line_Items
SELECT
    h.inv,
    CONCAT('SINV-', RIGHT(CONCAT('000000', h.inv), 6)),
    k.n,
    h.d,
    rm.Distributor_Code,
    rm.Retailer_Code,
    p.Product_ID,
    x.q,
    CAST(y.gross - y.tod AS decimal(18, 2)),
    CAST(y.tod AS decimal(18, 2)),
    CAST((y.gross - y.tod) * 1.18 AS decimal(18, 2))
FROM #sh h
JOIN #k k ON k.n <= 1 + ABS(CHECKSUM(CONCAT('sk', h.inv))) % 6
JOIN Silver.Retailer_Master rm ON rm.Retailer_Code = CONCAT('R', RIGHT(CONCAT('0000', h.r), 4))
JOIN Silver.Product_Master p
  ON p.Product_ID = CONCAT('SKU', RIGHT(CONCAT('000', ABS(CHECKSUM(CONCAT('sp', h.inv, '-', k.n))) % 16 + 1), 3))
CROSS APPLY (
    SELECT CAST((6 + ABS(CHECKSUM(CONCAT('sq', h.inv, '-', k.n))) % 60)
                * CASE WHEN MONTH(h.d) IN (3, 4, 5) THEN 2.0 WHEN MONTH(h.d) IN (7, 8) THEN 0.6 ELSE 1.0 END AS int) AS q
) x
CROSS APPLY (
    SELECT x.q * p.MRP_Per_Unit * 0.85 AS gross,
           x.q * p.MRP_Per_Unit * 0.85 * CASE h.r % 3 WHEN 0 THEN 0.04 WHEN 1 THEN 0.02 ELSE 0 END AS tod
) y;

CREATE TABLE Silver.Secondary_Invoices (
    Invoice_Id int NOT NULL,
    Invoice_Number varchar(20) NOT NULL,
    Date date NOT NULL,
    Distributor_Code varchar(10) NOT NULL,
    Retailer_Code varchar(10) NOT NULL,
    Quantity int NOT NULL,
    Amount_Excl_Tax decimal(18, 2) NOT NULL,
    TOD decimal(18, 2) NOT NULL,
    Amount_Incl_Tax decimal(18, 2) NOT NULL
);
INSERT Silver.Secondary_Invoices
SELECT Invoice_Id, Invoice_Number, Invoice_Date, Distributor_Code, Retailer_Code,
       SUM(Quantity), SUM(Amount_Excl_Tax), SUM(TOD), SUM(Amount_Incl_Tax)
FROM Silver.Secondary_Invoice_Line_Items
GROUP BY Invoice_Id, Invoice_Number, Invoice_Date, Distributor_Code, Retailer_Code;

------------------------------------------------------------------------------
-- Monthly hub targets
------------------------------------------------------------------------------
CREATE TABLE Silver.Hub_Target (
    Month date NOT NULL,
    Hub_Name nvarchar(50) NOT NULL,
    Primary_Target decimal(18, 2) NOT NULL,
    Secondary_Target decimal(18, 2) NOT NULL,
    CONSTRAINT PK_Hub_Target PRIMARY KEY (Month, Hub_Name)
);
INSERT Silver.Hub_Target
SELECT m.Month, h.Hub,
       3000000 * s.f,
       1500000 * s.f
FROM (SELECT DATEADD(month, n - 1, CAST('2025-01-01' AS date)) AS Month FROM #n WHERE n <= 22) m
CROSS JOIN (VALUES (N'North Karnataka'), (N'South Karnataka')) h(Hub)
CROSS APPLY (SELECT CASE WHEN MONTH(m.Month) IN (3, 4, 5) THEN 1.8
                         WHEN MONTH(m.Month) IN (7, 8) THEN 0.7 ELSE 1.0 END AS f) s;

------------------------------------------------------------------------------
-- Columnstore on fact tables (mirrors production per BUSINESS_CONTEXT)
------------------------------------------------------------------------------
CREATE CLUSTERED COLUMNSTORE INDEX CCI_Primary_Invoices ON Silver.Primary_Invoices;
CREATE CLUSTERED COLUMNSTORE INDEX CCI_Primary_Invoice_Line_Items ON Silver.Primary_Invoice_Line_Items;
CREATE CLUSTERED COLUMNSTORE INDEX CCI_Secondary_Invoices ON Silver.Secondary_Invoices;
CREATE CLUSTERED COLUMNSTORE INDEX CCI_Secondary_Invoice_Line_Items ON Silver.Secondary_Invoice_Line_Items;
GO

------------------------------------------------------------------------------
-- Read-only login, restricted to the Silver schema (what production should use)
------------------------------------------------------------------------------
USE master;
IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'mcp_reader') DROP LOGIN mcp_reader;
CREATE LOGIN mcp_reader WITH PASSWORD = 'Reader#Pass2026', CHECK_POLICY = OFF, DEFAULT_DATABASE = HangyoDev;
GO
USE HangyoDev;
CREATE USER mcp_reader FOR LOGIN mcp_reader;
GRANT SELECT ON SCHEMA::Silver TO mcp_reader;
GRANT VIEW DEFINITION ON SCHEMA::Silver TO mcp_reader;
GO

SELECT s.name + '.' + t.name AS table_name, SUM(p.rows) AS row_count
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
GROUP BY s.name, t.name
ORDER BY s.name, t.name;
GO
