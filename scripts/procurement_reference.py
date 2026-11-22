"""Independent reference calculation using Fraction on provider CSV, not Decimal audit outputs.

These are our declared snapshot-policy answers, not labels supplied by the provider.
No generator hints (qty_ratio, billing_drift, received_share) or payment status are used.
"""
import csv
from collections import Counter
from fractions import Fraction


def raw_tables(folder):
    names = ("buyers", "suppliers", "items", "purchase_orders", "po_lines", "goods_receipts", "supplier_invoices")
    data = {}
    for name in names:
        with (folder / "raw" / f"{name}.csv").open(encoding="utf-8-sig", newline="") as stream:
            data[name] = list(csv.DictReader(stream))
    return data


def expected(data, po_id):
    order = next(row for row in data["purchase_orders"] if row["id"] == str(po_id))
    supplier = next(row for row in data["suppliers"] if row["id"] == order["supplier_id"])
    buyer = next(row for row in data["buyers"] if row["id"] == order["buyer_id"])
    fields = {"order_id": order["id"], "po_number": order["po_number"], "supplier": supplier["supplier_name"],
              "buyer": buyer["first_name"] + " " + buyer["last_name"]}
    keys = []
    for line in [row for row in data["po_lines"] if row["purchase_order_id"] == order["id"]]:
        lid = line["id"]
        product = next(row for row in data["items"] if row["id"] == line["item_id"])
        for field, value in {"line_id": lid, "item_id": line["item_id"], "sku": product["sku"],
            "product": product["item_label"], "unit": product["unit"], "ordered_quantity": line["quantity"],
            "po_unit_price": line["unit_price"]}.items():
            fields[f"line/{lid}/{field}"] = value
        receipts = [row for row in data["goods_receipts"] if row["po_line_id"] == lid]
        invoices = [row for row in data["supplier_invoices"] if row["po_line_id"] == lid]
        quantity = Fraction(line["quantity"])
        rq = sum((Fraction(row["received_qty"]) for row in receipts), Fraction()) if receipts else None
        iq = sum((Fraction(row["invoiced_qty"]) for row in invoices), Fraction()) if invoices else None
        for qty, code in [(rq, "RECEIPT_ORDER_QTY"), (iq, "INVOICE_ORDER_QTY")]:
            status = "review" if qty is None else "difference" if qty > quantity else "info" if qty < quantity else "consistent"
            keys.append((lid, code, "", status))
        status = "review" if rq is None or iq is None else "difference" if iq > rq else "info" if iq < rq else "consistent"
        keys.append((lid, "INVOICE_RECEIPT_QTY", "", status))
        for invoice in invoices:
            status = "consistent" if Fraction(invoice["billed_unit_price"]) == Fraction(line["unit_price"]) else "difference"
            keys.append((lid, "UNIT_PRICE", invoice["id"], status))
            for target, column in {"invoice_id": "id", "invoice_number": "invoice_no", "invoiced_quantity": "invoiced_qty",
                                   "unit_price": "billed_unit_price", "invoice_date": "invoice_date"}.items():
                fields[f"line/{lid}/invoice/{invoice['id']}/{target}"] = invoice[column]
        if not invoices:
            keys.append((lid, "UNIT_PRICE", "", "review"))
        if len(invoices) > 1:
            keys.append((lid, "MULTIPLE_INVOICES", "", "info"))
        for receipt in receipts:
            keys.append((lid, "RECEIPT_CONDITION", receipt["id"], "consistent" if receipt["condition"] == "good" else "review"))
            for target, column in {"receipt_id": "id", "received_quantity": "received_qty", "received_on": "received_on", "condition": "condition"}.items():
                fields[f"line/{lid}/receipt/{receipt['id']}/{target}"] = receipt[column]
    return fields, Counter(keys)


def flatten(order):
    result = {key: order[key] for key in ("order_id", "po_number", "supplier", "buyer")}
    for line in order["lines"]:
        prefix = "line/" + line["line_id"]
        for key in ("line_id", "item_id", "sku", "product", "unit", "ordered_quantity", "po_unit_price"):
            result[prefix + "/" + key] = line[key]
        for category, identity, names in [
            ("invoice", "invoice_id", ("invoice_id", "invoice_number", "invoiced_quantity", "unit_price", "invoice_date")),
            ("receipt", "receipt_id", ("receipt_id", "received_quantity", "received_on", "condition"))]:
            for record in line["invoices" if category == "invoice" else "receipts"]:
                for name in names:
                    result[f"{prefix}/{category}/{record[identity]}/{name}"] = record[name]
    return result
