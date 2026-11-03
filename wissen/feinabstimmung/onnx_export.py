"""Feinabgestimmtes e5 als ONNX fuer fastembed: Eingaben input_ids und
attention_mask (int64), Ausgabe last_hidden_state -- wie das Original
qdrant/multilingual-e5-large-onnx. Tokenizer-Dateien vom Original.
    onnx_export.py <modellordner> <zielordner> <original-snapshot>"""
import os, shutil, sys, tempfile
import onnx, torch
from transformers import AutoModel

QUELLE, ZIEL, ORIGINAL = sys.argv[1:4]
m = AutoModel.from_pretrained(QUELLE, torch_dtype=torch.float32).eval()

class Huelle(torch.nn.Module):
    def __init__(self, m):
        super().__init__(); self.m = m
    def forward(self, input_ids, attention_mask):
        return self.m(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state

beispiel = (torch.ones(2, 16, dtype=torch.long), torch.ones(2, 16, dtype=torch.long))
os.makedirs(ZIEL, exist_ok=True)
with tempfile.TemporaryDirectory(dir=ZIEL) as t:
    roh = os.path.join(t, "roh.onnx")
    torch.onnx.export(Huelle(m), beispiel, roh, input_names=["input_ids", "attention_mask"],
                      output_names=["last_hidden_state"], opset_version=17, dynamo=False,
                      dynamic_axes={"input_ids": {0: "batch_size", 1: "sequence_length"},
                                    "attention_mask": {0: "batch_size", 1: "sequence_length"},
                                    "last_hidden_state": {0: "batch_size", 1: "sequence_length"}})
    modell = onnx.load(roh)
    onnx.save_model(modell, os.path.join(ZIEL, "model.onnx"), save_as_external_data=True,
                    all_tensors_to_one_file=True, location="model.onnx_data")
for f in ("tokenizer.json", "config.json", "special_tokens_map.json", "tokenizer_config.json"):
    shutil.copy(os.path.join(ORIGINAL, f), os.path.join(ZIEL, f))
print("exportiert nach", ZIEL, sorted(os.listdir(ZIEL)))
