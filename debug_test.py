"""
Debug script to test backend components
"""
import asyncio
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from llm_client import LLMClient
from pdf_utils import extract_text_from_pdf, chunk_text

async def test_llm_client():
    """Test LLM client initialization and embedding"""
    print("\n" + "="*50)
    print("Testing LLM Client")
    print("="*50)
    
    try:
        llm = LLMClient()
        print("✓ LLMClient initialized successfully")
        
        # Test embedding
        print("\nTesting embedding with sample text...")
        test_text = ["Hello world", "This is a test"]
        embeddings = await llm.embed(test_text)
        print(f"✓ Embeddings created: {len(embeddings)} texts")
        print(f"  - Embedding size: {len(embeddings[0])} dimensions")
        return True
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

async def test_pdf_utils(pdf_path):
    """Test PDF extraction"""
    print("\n" + "="*50)
    print("Testing PDF Utils")
    print("="*50)
    
    try:
        if not Path(pdf_path).exists():
            print(f"✗ PDF file not found: {pdf_path}")
            return False
            
        # Read PDF
        with open(pdf_path, 'rb') as f:
            content = f.read()
        print(f"✓ PDF read: {len(content)} bytes")
        
        # Extract text
        text = extract_text_from_pdf(content)
        print(f"✓ Text extracted: {len(text)} characters")
        if len(text.strip()) == 0:
            print("✗ Warning: Extracted text is empty!")
            return False
        
        # Chunk text
        chunks = chunk_text(text, max_tokens=400)
        print(f"✓ Text chunked: {len(chunks)} chunks")
        for i, chunk in enumerate(chunks[:3]):
            preview = chunk[:100].replace('\n', ' ')
            print(f"  - Chunk {i+1}: {preview}...")
        
        return True
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    print("\n" + "="*50)
    print("BACKEND DEBUG TEST")
    print("="*50)
    
    # Test 1: LLM Client
    llm_ok = await test_llm_client()
    
    # Test 2: PDF Utils (optional - use test PDF if available)
    pdf_ok = True
    test_pdfs = [
        "sample.pdf",
        "test.pdf",
        "../sample.pdf"
    ]
    
    for pdf_path in test_pdfs:
        if Path(pdf_path).exists():
            pdf_ok = await test_pdf_utils(pdf_path)
            break
    else:
        print("\n" + "="*50)
        print("No test PDF found (optional)")
        print("="*50)
    
    # Summary
    print("\n" + "="*50)
    print("TEST SUMMARY")
    print("="*50)
    print(f"LLM Client:  {'✓ PASS' if llm_ok else '✗ FAIL'}")
    print(f"PDF Utils:   {'✓ PASS' if pdf_ok else '✗ FAIL'}")
    
    if llm_ok and pdf_ok:
        print("\n✓ All components working! Backend should be ready.")
    else:
        print("\n✗ Some components failed. Check logs above.")

if __name__ == "__main__":
    asyncio.run(main())
