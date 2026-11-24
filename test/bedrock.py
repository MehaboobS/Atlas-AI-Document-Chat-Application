"""
Atlas AI - 100% Free Tier Architecture Integration Test
- Chat LLM: Google Gemini 3.5 Flash (100% Free via Google AI Studio)
- Embeddings: Hugging Face Open-Source (BAAI/bge-small-en-v1.5) via FastEmbed (100% Free & Local)
- Storage: AWS S3 Free Tier (5GB Standard Storage) for documents & embeddings
"""

import os
import json
from dotenv import load_dotenv
import boto3
from google import genai
from fastembed import TextEmbedding

load_dotenv()

print("=" * 60)
print(" ATLAS AI: 100% FREE TIER ARCHITECTURE TEST")
print("=" * 60)

# -------------------------------------------------------------
# Test 1: Hugging Face Open-Source Embeddings (Free & Local)
# -------------------------------------------------------------
print("\n[Test 1] Generating Hugging Face Open-Source Embeddings...")
model_name = os.getenv("HF_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
embedding_model = TextEmbedding(model_name=model_name)

sample_text = "Explain Retrieval-Augmented Generation (RAG) in document search."
embeddings = list(embedding_model.embed([sample_text]))[0]

print(f" Model: {model_name}")
print(f" Input text: '{sample_text}'")
print(f" Embedding dimensions: {len(embeddings)}")
print(f" First 5 vector components: {[round(float(x), 4) for x in embeddings[:5]]}")
print(" [SUCCESS] Hugging Face embeddings generated locally for free!")

# -------------------------------------------------------------
# Test 2: AWS S3 Storage (Documents & Embeddings)
# -------------------------------------------------------------
print("\n[Test 2] Testing AWS S3 Storage for Documents & Embeddings...")
s3_bucket = os.getenv("S3_BUCKET", "atlas-enterprice-1126").strip()
s3 = boto3.client(
    "s3",
    region_name=os.getenv("AWS_REGION", "ap-southeast-2"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)

test_doc_id = "test-doc-001"
doc_s3_key = f"documents/{test_doc_id}/sample.txt"
embeddings_s3_key = f"embeddings/{test_doc_id}.json"

try:
    # 2a. Store a sample document
    sample_doc_content = b"This is a test document stored in AWS S3 Free Tier storage."
    s3.put_object(
        Bucket=s3_bucket,
        Key=doc_s3_key,
        Body=sample_doc_content,
        ContentType="text/plain",
    )
    print(f" Document saved to: s3://{s3_bucket}/{doc_s3_key}")

    # 2b. Store generated embeddings and metadata
    embedding_payload = {
        "doc_id": test_doc_id,
        "model": model_name,
        "dimensions": len(embeddings),
        "chunks": [
            {
                "chunk_id": 0,
                "text": sample_text,
                "embedding": [float(x) for x in embeddings[:10]],
            }
        ],
    }
    s3.put_object(
        Bucket=s3_bucket,
        Key=embeddings_s3_key,
        Body=json.dumps(embedding_payload),
        ContentType="application/json",
    )
    print(f" Embeddings saved to: s3://{s3_bucket}/{embeddings_s3_key}")

    # 2c. Verify retrieval from S3
    read_back = s3.get_object(Bucket=s3_bucket, Key=embeddings_s3_key)
    stored_data = json.loads(read_back["Body"].read().decode("utf-8"))
    print(f" Verified read from S3: doc_id={stored_data['doc_id']}, model={stored_data['model']}")
    print(" [SUCCESS] AWS S3 document and embeddings storage verified!")
except Exception as e:
    print(f" S3 Note/Error: {type(e).__name__}: {e}")

# -------------------------------------------------------------
# Test 3: Google Gemini Chat Model (Official Google GenAI SDK)
# -------------------------------------------------------------
print("\n[Test 3] Testing Google Gemini Chat Model...")
gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

if not gemini_key:
    print(" [NOTE] GEMINI_API_KEY is not set in .env yet.")
    print(" -> Get your 100% FREE API key at: https://aistudio.google.com/")
else:
    try:
        client = genai.Client(api_key=gemini_key)
        response = client.models.generate_content(
            model=gemini_model,
            contents="Explain RAG in one sentence.",
        )
        print(f" Model: {gemini_model}")
        print(f" Gemini Response:\n{response.text.strip()}")
        print(" [SUCCESS] Google Gemini response received successfully!")
    except Exception as e:
        print(f" Gemini API Error: {type(e).__name__}: {e}")

print("\n" + "=" * 60)
print(" ALL TESTS COMPLETED")
print("=" * 60)
