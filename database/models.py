"""SQLite schema definitions (DDL) for Stock Sentry.

Optional business metrics (such as cost_price, selling_price, reorder_point,
target_stock, delivery_days, reliability_score) allow NULL so dynamically
uploaded user datasets never require fabricated placeholder values.
"""

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS products (
        product_id TEXT PRIMARY KEY,
        product_name TEXT NOT NULL,
        category TEXT DEFAULT 'Uncategorized',
        current_stock INTEGER NOT NULL DEFAULT 0 CHECK (current_stock >= 0),
        unit TEXT DEFAULT 'unit',
        selling_price REAL CHECK (selling_price IS NULL OR selling_price > 0),
        cost_price REAL CHECK (cost_price IS NULL OR cost_price > 0),
        reorder_point INTEGER CHECK (reorder_point IS NULL OR reorder_point >= 0),
        target_stock INTEGER CHECK (target_stock IS NULL OR target_stock > 0),
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sales (
        sale_id TEXT PRIMARY KEY,
        date TEXT,
        product_id TEXT NOT NULL,
        quantity_sold INTEGER NOT NULL CHECK (quantity_sold > 0),
        selling_price REAL CHECK (selling_price IS NULL OR selling_price > 0),
        FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier_id TEXT NOT NULL,
        supplier_name TEXT NOT NULL,
        product_id TEXT NOT NULL,
        unit_price REAL CHECK (unit_price IS NULL OR unit_price > 0),
        delivery_days INTEGER CHECK (delivery_days IS NULL OR delivery_days >= 1),
        available_quantity INTEGER CHECK (available_quantity IS NULL OR available_quantity >= 0),
        reliability_score REAL CHECK (reliability_score IS NULL OR (reliability_score >= 0 AND reliability_score <= 100)),
        min_order_qty INTEGER DEFAULT 1 CHECK (min_order_qty IS NULL OR min_order_qty >= 1),
        UNIQUE(supplier_id, product_id),
        FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_movements (
        movement_id TEXT PRIMARY KEY,
        date TEXT,
        product_id TEXT NOT NULL,
        movement_type TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        remarks TEXT DEFAULT '',
        FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS purchase_orders (
        order_id TEXT PRIMARY KEY,
        product_id TEXT NOT NULL,
        product_name TEXT NOT NULL,
        supplier_id TEXT NOT NULL,
        supplier_name TEXT NOT NULL,
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        unit_price REAL CHECK (unit_price IS NULL OR unit_price > 0),
        total_cost REAL CHECK (total_cost IS NULL OR total_cost > 0),
        delivery_days INTEGER CHECK (delivery_days IS NULL OR delivery_days >= 1),
        status TEXT NOT NULL DEFAULT 'Pending Approval',
        reason TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        created_by TEXT NOT NULL DEFAULT 'Procurement Agent',
        FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT NOT NULL,
        action TEXT NOT NULL,
        previous_status TEXT,
        new_status TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        actor TEXT NOT NULL,
        changes TEXT DEFAULT '',
        FOREIGN KEY (order_id) REFERENCES purchase_orders(order_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sales_product_date ON sales(product_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_movements_product_date ON stock_movements(product_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_suppliers_product ON suppliers(product_id)",
    "CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_orders(status)",
    "CREATE INDEX IF NOT EXISTS idx_audit_order ON audit_logs(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_suppliers_product_avail ON suppliers(product_id, available_quantity)",
    "CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date)",
    "CREATE INDEX IF NOT EXISTS idx_po_product_status ON purchase_orders(product_id, status)",
]
