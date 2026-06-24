from odoo import models, fields, api, _
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class StockReplenishWizard(models.TransientModel):
    _name = "stock.replenish.wizard"
    _description = "Wizard de Réassort"

    user_id = fields.Many2one(
        "res.users", string="Utilisateur", default=lambda self: self.env.user
    )
    # pos = fields.Many2one(
    #     "pos.config",
    #     string="Point de Vente",
    #     default=lambda self: self.env.user.pos_config_id.id,
    #     readonly=True,
    # )
    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Entrepôt Cible",
        default=lambda self: self.env.user.property_warehouse_id.id,
        readonly=True,
    )

    line_ids = fields.One2many(
        "stock.replenish.wizard.line",
        "wizard_id",
        string="Lignes de Réassort",
    )
    
    date_end   = fields.Date(
        string="Date de fin", default=fields.Date.context_today
    )
    
    date_start = fields.Date(string="Date de Début",default=fields.Date.context_today)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        date_start = res.get("date_start") or fields.Date.context_today(self)
        date_end = res.get("date_end") or fields.Date.context_today(self)
        res["line_ids"] = self._build_replenish_lines(date_start, date_end)
        return res

    def action_recompute(self):
        """Refresh the replenish lines based on the selected dates.

        This is an explicit user action (button) so it never runs on save
        and therefore never wipes the user's selections unexpectedly.
        """
        self.ensure_one()
        self.line_ids = [(5, 0, 0)] + self._build_replenish_lines(
            self.date_start, self.date_end
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "stock.replenish.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
            "context": self.env.context,
        }

    def action_create_picking(self):
        # _logger.info("ℹ️ line meets the requirements: %s", self.line_ids)
        for line in self.line_ids:
            _logger.info(
            "product=%s selected=%s sales=%s",
            line.product_id.display_name,
            line.to_process,
            line.quantity_sales,
            )   
            
        
        if not self:
            return True
        lines = self.line_ids.filtered(
            lambda line: line.product_id
            and line.to_process  # line.product_id and line.qty_need > 0 and
        )

        # _logger.info("ℹ️ lines : %s", lines)
        if not lines:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Aucun produit sélectionné"),
                    "message": _(
                        "Veuillez sélectionner au moins un produit à transférer."
                    ),
                    "type": "warning",
                },
            }

        transfer_lines = []
        for line in lines:
            # _logger.info("ℹ️ user pos configs: %s", quant)
            transfer_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                        "qty_demande": line.quantity_sales,
                    },
                )
            )

        if transfer_lines:
            # Create the transfer request record
            new_transfer = (
                self.env["demande.transfer"]
                .sudo()
                .create(
                    {
                        "line_ids": transfer_lines,
                    }
                )
            )
            
        new_context = dict(self.env.context)
        new_context.update(
            {
                "create": True,
                "edit": True,
                "delete": True,
            }
        )

        # Automatically redirect the user to the newly created transfer request form view
        return {
            "name": _("Demande de Transfert Créée"),
            "type": "ir.actions.act_window",
            "res_model": "demande.transfer",
            "view_mode": "form",
            "res_id": new_transfer.id,
            "target": "current",
            "context": new_context,
        }

    def _build_replenish_lines(self, date_start, date_end):
        """Build and return the (0, 0, vals) command list for line_ids.

        This is a plain helper (NOT a compute). It is called explicitly from
        default_get and action_recompute so it never re-runs on save and never
        wipes the user's `to_process` selections.
        """
        user = self.env.user

        if not user.property_warehouse_id:
            return []

        user_location = user.property_warehouse_id.lot_stock_id
        # _logger.info("User Location: %s", user_location)
        dp_location = (
            self.env["stock.location"]
            .sudo()
            .search([("complete_name", "=", "DP/Stock")], limit=1)
        )
        dp_location_id = dp_location.id if dp_location else 50

        # 1. Gather Stock Map
        user_quants = (
            self.env["stock.quant"]
            .sudo()
            .search(
                [
                    ("location_id", "child_of", user_location.id),
                    ("quantity", ">=", 1),
                ]
            )
        )

        user_stock_map = {}
        for q in user_quants:
            user_stock_map[q.product_id.id] = (
                user_stock_map.get(q.product_id.id, 0.0) + q.quantity
            )

        # 2. Main Depot Quants
        dp_quants = (
            self.env["stock.quant"]
            .sudo()
            .search(
                [("location_id", "child_of", dp_location_id), ("quantity", ">=", 1)]
            )
        )

        # 3. History Dates
        start_str = fields.Date.to_date(date_start).strftime("%Y-%m-%d")
        end_str = fields.Date.to_date(date_end).strftime("%Y-%m-%d")

        # 4. Sales Map
        sales_data = (
            self.env["report.pos.order"]
            .sudo()
            .read_group(
                domain=[
                    ("date", ">=", f"{start_str} 00:00:00"),
                    ("date", "<=", f"{end_str} 23:59:59"),
                    ("config_id", "in", user.pos_config_ids.ids),
                ],
                fields=["product_qty", "product_id"],
                groupby=["product_id"],
            )
        )

        sales_map = {}

        if not sales_data:
            return []

        for line in sales_data:
            if line.get("product_id"):
                prod_id = line["product_id"][0]
                sales_map[prod_id] = line["product_qty"]

        # ==========================================
        # Prepare lines for the Wizard UI
        # ==========================================
        wizard_lines = []

        for quant in dp_quants:
            product = quant.product_id
            qty_sales = sales_map.get(product.id, 0.0)
            qty_stock = user_stock_map.get(product.id, 0.0)

            # Only add to UI if they sold it and stock is below sales
            if qty_sales > 0 and qty_sales > qty_stock:
                wizard_lines.append(
                    (
                        0,
                        0,
                        {
                            "product_id": product.id,
                            "quantity_dp": quant.quantity,
                            "quantity_stock": qty_stock,
                            "quantity_sales": qty_sales,
                            "quantity_need": qty_sales,
                        },
                    )
                )

        return wizard_lines


class StockReplenishWizardLine(models.TransientModel):
    _name = "stock.replenish.wizard.line"
    _description = "Ligne de Réassort"
    image_128 = fields.Binary(
        related="product_id.image_128", string="Image", prefetch=True
    )
    to_process = fields.Boolean(string="Sélectionner", default=False)
    wizard_id = fields.Many2one("stock.replenish.wizard", ondelete="cascade")
    product_id = fields.Many2one("product.product", string="Produit")
    quantity_dp = fields.Float(string="Qté Dépôt Principal")
    quantity_stock = fields.Float(string="Qté Emplacement Actuel")
    quantity_sales = fields.Float(string="Ventes (Semaine Dernière)")
    quantity_need = fields.Float(string="Qté Nécessaire (Besoin)")
