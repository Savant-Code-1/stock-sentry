"""Tests for procurement calculations, budget validation, human approval, audit logs, and supervisor routing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.supervisor_agent import SupervisorAgent
from backend import procurement
from database.seed import seed_database_if_empty
from utils.validators import ValidationError


class TestProcurementAndWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_proc.db"
        seed_database_if_empty(db_path=self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_suggested_order_quantity_formula_and_guards(self) -> None:
        # Target - Current - Incoming = 50 - 10 - 5 = 35
        qty, _ = procurement.calculate_suggested_order_quantity(
            target_stock=50, current_stock=10, incoming_stock=5
        )
        self.assertEqual(qty, 35)

        # Never negative when stock + incoming >= target
        qty_neg, _ = procurement.calculate_suggested_order_quantity(
            target_stock=30, current_stock=25, incoming_stock=10
        )
        self.assertEqual(qty_neg, 0)

        # MOQ adjustment
        qty_moq, notes = procurement.calculate_suggested_order_quantity(
            target_stock=25, current_stock=20, incoming_stock=0, min_order_qty=10, available_quantity=50
        )
        self.assertEqual(qty_moq, 10)
        self.assertTrue(any("MOQ" in n for n in notes))

        # Supplier availability cap
        qty_cap, notes_cap = procurement.calculate_suggested_order_quantity(
            target_stock=80, current_stock=10, incoming_stock=0, available_quantity=25
        )
        self.assertEqual(qty_cap, 25)
        self.assertTrue(any("availability" in n.lower() for n in notes_cap))

    def test_budget_validation_enforced_on_po_creation(self) -> None:
        recs = procurement.generate_purchase_recommendations(budget_limit=50000.0, db_path=self.db_path)
        self.assertGreater(len(recs), 0)
        sample = recs[0]
        # Force a tiny budget limit below total cost -> must raise ValidationError
        with self.assertRaises(ValidationError):
            procurement.create_purchase_order(
                product_id=sample["product_id"],
                supplier_id=sample["supplier_id"],
                quantity=sample["suggested_quantity"],
                budget_limit=10.0,
                db_path=self.db_path,
            )

    def test_human_approval_modify_reject_and_audit_trail(self) -> None:
        recs = procurement.generate_purchase_recommendations(db_path=self.db_path)
        sample = recs[0]

        # 1. Create Draft PO -> Pending Approval
        po = procurement.create_purchase_order(
            product_id=sample["product_id"],
            supplier_id=sample["supplier_id"],
            quantity=sample["suggested_quantity"],
            reason="Test reorder",
            db_path=self.db_path,
        )
        self.assertEqual(po["status"], "Pending Approval")

        # 2. Modify PO -> Modified
        new_qty = max(1, sample["suggested_quantity"] - 2)
        mod_po = procurement.modify_purchase_order(
            order_id=po["order_id"],
            new_quantity=new_qty,
            actor="Owner Raj",
            note="Adjusted based on cash flow",
            db_path=self.db_path,
        )
        self.assertEqual(mod_po["status"], "Modified")
        self.assertEqual(mod_po["quantity"], new_qty)

        # 3. Approve PO -> Approved
        app_po = procurement.approve_purchase_order(
            order_id=po["order_id"],
            actor="Owner Raj",
            note="Approved for morning dispatch",
            db_path=self.db_path,
        )
        self.assertEqual(app_po["status"], "Approved")

        # 4. Audit trail contains CREATED -> MODIFIED -> APPROVED in full detail
        logs = procurement.get_audit_logs(order_id=po["order_id"], db_path=self.db_path)
        actions = list(logs["action"])
        self.assertIn("CREATED", actions)
        self.assertIn("MODIFIED", actions)
        self.assertIn("APPROVED", actions)

        # 5. Rejection test on a second PO
        po2 = procurement.create_purchase_order(
            product_id=sample["product_id"],
            supplier_id=sample["supplier_id"],
            quantity=5,
            reason="Second test PO",
            db_path=self.db_path,
        )
        rej_po = procurement.reject_purchase_order(
            order_id=po2["order_id"], actor="Owner Raj", note="Not needed this week", db_path=self.db_path
        )
        self.assertEqual(rej_po["status"], "Rejected")

    def test_supervisor_agent_end_to_end_queries(self) -> None:
        sup = SupervisorAgent(db_path=self.db_path)
        for q in [
            "Which products are running low?",
            "Which products may run out in the next five days?",
            "Which products should I reorder today?",
            "Which supplier is best for cooking oil?",
            "Why was this anomaly detected?",
            "Generate a purchase recommendation.",
            "How much inventory value do I currently have?",
        ]:
            resp = sup.answer_query(q)
            self.assertTrue(resp["answer"])
            self.assertGreaterEqual(len(resp["agents_used"]), 2)


if __name__ == "__main__":
    unittest.main()
