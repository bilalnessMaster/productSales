{
    "name": "Product Seuil",
    "version": "1.0",
    "summary": "module desgin for cashier to take a look what products he has in stock and what he sold in last week",
    "author": "bilal habib allah",
    "depends": [
        "base",
        "product",
        "stock",
        "point_of_sale",
        "stock_alert",
    ],
    "data": ["security/ir.model.access.csv", "views/view_catalogue.xml"],
    "installable": True,
}
