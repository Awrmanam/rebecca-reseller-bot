from app.payments.plisio import signature,verify

def test_valid_invalid_and_missing_signature():
 p={"order_number":"A1","status":"completed","source_amount":"12.00","txn_id":"x"}; p["verify_hash"]=signature(p,"secret")
 assert verify(p,"secret"); assert not verify(p,"wrong"); assert not verify({"status":"completed"},"secret")
def test_signature_canonical_nested():
 a={"b":{"z":2,"a":1},"a":[2,1]}; b={"a":[2,1],"b":{"a":1,"z":2}}
 assert signature(a,"s")==signature(b,"s")
