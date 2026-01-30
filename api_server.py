"""
AI Battle Arena - Ultra-lightweight RAG System
No external model dependencies - works offline immediately
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict
import uvicorn
import warnings
warnings.filterwarnings('ignore')

# Minimal imports
from PyPDF2 import PdfReader
import requests
from io import BytesIO
import os
import re
import json
try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

# Load response preferences
def load_preferences():
    try:
        with open('response_preferences.json', 'r') as f:
            return json.load(f)
    except:
        return {
            "output_format": {"style": "continuous", "flow": "connected"},
            "transition_words": ["Additionally", "Furthermore", "Moreover"]
        }

PREFERENCES = load_preferences()

# ============================================================================
# CONFIGURATION
# ============================================================================

RAG_CONFIG = {
    "chunk_size": 512,
    "chunk_overlap": 128,
    "top_k_chunks": 5,
}

# ============================================================================
# SIMPLE TEXT-BASED RETRIEVAL
# ============================================================================

class SimpleTextRetrieval:
    """Ultra-simple text-based retrieval without ML"""
    
    def __init__(self):
        self.chunks = []
    
    def add_chunks(self, chunks: List[Dict]) -> bool:
        """Store chunks for retrieval"""
        self.chunks = chunks
        return True
    
    def retrieve(self, query: str, top_k: int = 5) -> List[Dict]:
        """Retrieve relevant chunks based on keyword matching and similarity"""
        if not self.chunks:
            return []
        
        query_words = set(w.lower() for w in query.split() if len(w) > 2)
        
        # Score chunks based on keyword overlap
        scored_chunks = []
        for chunk in self.chunks:
            text = chunk['text'].lower()
            
            # Count keyword matches
            matches = sum(1 for word in query_words if word in text)
            
            # Bonus for exact phrase matches
            phrase_bonus = 0
            if len(query) > 5:
                query_phrase = query.lower()
                if query_phrase in text:
                    phrase_bonus = len(query_words) * 2
            
            score = matches + phrase_bonus
            
            if score > 0:
                scored_chunks.append((score, chunk))
        
        # If no matches with strict keyword search, return longest chunks
        if not scored_chunks:
            scored_chunks = [(len(c['text']), c) for c in self.chunks]
        
        # Sort by score and return top-k
        scored_chunks.sort(reverse=True, key=lambda x: x[0])
        return [chunk for _, chunk in scored_chunks[:top_k]]

# ============================================================================
# RAG PIPELINE
# ============================================================================

class RAGPipeline:
    """Lightweight RAG without ML dependencies"""
    
    def __init__(self):
        self.retrieval = SimpleTextRetrieval()
        self.pdf_cache = {}
        print("✓ RAG Pipeline: Initialized (Text-based)")
    
    def download_pdf(self, pdf_url: str) -> bytes:
        """Download PDF from URL"""
        try:
            print(f"  Downloading: {pdf_url[:60]}...")
            response = requests.get(pdf_url, timeout=30)
            response.raise_for_status()
            print(f"  ✓ Downloaded {len(response.content)} bytes")
            return response.content
        except Exception as e:
            print(f"  ✗ Download failed: {e}")
            return None
    
    def extract_text_from_pdf(self, pdf_content: bytes) -> str:
        """Extract text from PDF with OCR fallback"""
        try:
            pdf_reader = PdfReader(BytesIO(pdf_content))
            full_text = ""
            
            # First try standard text extraction
            for page_num, page in enumerate(pdf_reader.pages):
                try:
                    text = page.extract_text()
                    if text and len(text.strip()) > 0:
                        full_text += f"{text}\n\n"
                except:
                    pass
            
            # If we got reasonable text, return it
            if len(full_text) > 200:
                print(f"  ✓ Extracted {len(full_text)} characters from PDF (text-based)")
                return full_text
            
            # Try OCR if text extraction failed
            if TESSERACT_AVAILABLE and len(full_text) < 200:
                print("  ℹ Text extraction minimal, attempting OCR...")
                try:
                    from pdf2image import convert_from_bytes
                    images = convert_from_bytes(pdf_content, first_page=1, last_page=min(10, len(pdf_reader.pages)))
                    
                    for page_num, image in enumerate(images):
                        try:
                            text = pytesseract.image_to_string(image)
                            if text and len(text.strip()) > 0:
                                full_text += f"{text}\n\n"
                        except Exception as ocr_error:
                            print(f"    OCR page {page_num} error: {ocr_error}")
                    
                    if len(full_text) > 200:
                        print(f"  ✓ Extracted {len(full_text)} characters via OCR")
                        return full_text
                except Exception as pdf2img_error:
                    print(f"  ℹ OCR not available: {pdf2img_error}")
            
            if len(full_text) > 0:
                print(f"  ✓ Extracted {len(full_text)} characters from PDF")
                return full_text
            
            return ""
        except Exception as e:
            print(f"  ✗ Extraction error: {e}")
            return ""
    
    def chunk_text(self, text: str) -> List[Dict]:
        """Split text into meaningful chunks preserving context"""
        chunks = []
        
        # Normalize paragraph breaks
        text = re.sub(r'\n{2,}', '\n\n', text)
        
        # Split by paragraphs first
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        
        current_chunk = ""
        
        for para in paragraphs:
            if len(para) < 10:
                continue
            
            # If adding this paragraph exceeds chunk size, save current chunk
            if current_chunk and len(current_chunk) + len(para) > RAG_CONFIG["chunk_size"]:
                if len(current_chunk.strip()) > 20:
                    chunks.append({"text": current_chunk.strip(), "page_num": 0})
                current_chunk = para
            else:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
        
        # Add remaining chunk
        if current_chunk and len(current_chunk.strip()) > 20:
            chunks.append({"text": current_chunk.strip(), "page_num": 0})
        
        # Further split large chunks by sentences
        final_chunks = []
        for chunk in chunks:
            text = chunk['text']
            if len(text) > RAG_CONFIG["chunk_size"]:
                sentences = re.split(r'(?<=[.!?])\s+', text)
                sub_chunk = ""
                for sent in sentences:
                    if len(sub_chunk) + len(sent) < RAG_CONFIG["chunk_size"]:
                        sub_chunk += (" " if sub_chunk else "") + sent
                    else:
                        if len(sub_chunk.strip()) > 20:
                            final_chunks.append({"text": sub_chunk.strip(), "page_num": 0})
                        sub_chunk = sent
                if sub_chunk and len(sub_chunk.strip()) > 20:
                    final_chunks.append({"text": sub_chunk.strip(), "page_num": 0})
            else:
                final_chunks.append(chunk)
        
        return final_chunks
    
    def process_pdf(self, pdf_url: str) -> bool:
        """Download, extract, and index PDF"""
        if pdf_url in self.pdf_cache:
            print(f"  ✓ Using cached PDF")
            return True
        
        print(f"🔄 Processing PDF...")
        
        # Download
        pdf_content = self.download_pdf(pdf_url)
        if not pdf_content:
            return False
        
        # Extract text
        text = self.extract_text_from_pdf(pdf_content)
        if not text or len(text.strip()) < 50:
            print("  ✗ Failed to extract sufficient text from PDF")
            print("     Try: 1) Different PDF URL, 2) Ensure PDF has readable text, 3) Check Tesseract OCR installation")
            return False
        
        # Chunk
        chunks = self.chunk_text(text)
        if not chunks:
            print("  ✗ No chunks created")
            return False
        
        # Index
        self.retrieval.add_chunks(chunks)
        self.pdf_cache[pdf_url] = True
        print(f"  ✓ Created {len(chunks)} chunks for indexing")
        return True
    
    def detect_special_format(self, question: str) -> str:
        """Detect if user is asking for special format like points, list, bullets, etc"""
        question_lower = question.lower()
        
        # Check for points format request
        if any(word in question_lower for word in ['points', 'point', 'bullet', 'bullets', 'list', 'summary']):
            if any(word in question_lower for word in ['points', 'bullet', 'list', 'numbered']):
                return 'points'
            elif 'summary' in question_lower:
                return 'summary'
        
        return 'normal'
    
    def format_as_points(self, context: str, num_points: int = 10) -> str:
        """Format context as numbered points"""
        # Clean and split into sentences
        context = self.clean_text(context)
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', context) if s.strip() and len(s) > 15]
        
        if len(sentences) < num_points:
            num_points = len(sentences)
        
        # Score sentences for relevance and uniqueness
        scored = []
        for i, sent in enumerate(sentences):
            # Avoid duplicates by checking if this sentence is too similar to already scored ones
            is_duplicate = False
            sent_words = set(re.findall(r'\b\w+\b', sent.lower()))
            
            for _, _, scored_sent in scored:
                scored_words = set(re.findall(r'\b\w+\b', scored_sent.lower()))
                overlap = len(sent_words & scored_words)
                if overlap > len(sent_words) * 0.5:  # More than 50% overlap means duplicate
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                # Score based on length and position
                score = len(sent) / 100 + (1 / (i + 1)) * 0.3
                scored.append((score, i, sent))
        
        # Sort by score but maintain some document order
        scored.sort(key=lambda x: (-x[0], x[1]))
        
        # Get top points
        points = sorted([s[2] for s in scored[:num_points]], key=lambda x: sentences.index(x) if x in sentences else 999)
        
        # Format as numbered list
        formatted = "Summary (Key Points):\n\n"
        for i, point in enumerate(points, 1):
            # Clean up point
            point = point.strip()
            if not point.endswith(('.', '!', '?')):
                point += '.'
            formatted += f"{i}. {point}\n"
        
        return formatted
    
    def format_as_summary(self, context: str) -> str:
        """Format context as a coherent summary with proper sentences"""
        context = self.clean_text(context)
        
        # Split into paragraphs first for better context
        paragraphs = [p.strip() for p in context.split('\n\n') if p.strip() and len(p) > 50]
        
        if not paragraphs:
            # Fallback to sentences if no paragraphs
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', context) if s.strip() and len(s) > 20]
            if len(sentences) < 3:
                return context[:500] + "..." if len(context) > 500 else context
            
            # Select well-formed sentences
            good_sentences = []
            for sent in sentences:
                # Filter out table of contents, headers, page numbers
                if re.match(r'^[\d\.\s]+$', sent):  # Only numbers and dots
                    continue
                if len(sent.split()) < 5:  # Too short
                    continue
                if sent.count('.') > 5:  # Too many dots (likely TOC)
                    continue
                good_sentences.append(sent)
            
            # Take first few good sentences
            summary_sentences = good_sentences[:5]
            return " ".join(summary_sentences)
        
        # Extract key sentences from paragraphs
        summary_parts = []
        for para in paragraphs[:5]:  # First 5 paragraphs
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
            if sentences:
                # Get first substantial sentence from each paragraph
                for sent in sentences:
                    if len(sent.split()) >= 5 and not re.match(r'^[\d\.\s]+', sent):
                        summary_parts.append(sent)
                        break
        
        if not summary_parts:
            return "The document contains information on computer security, cryptography, and risk assessment procedures."
        
        # Combine into coherent summary
        summary = " ".join(summary_parts[:3])  # Use first 3 good sentences
        
        # Truncate if too long
        if len(summary) > 600:
            sentences = re.split(r'(?<=[.!?])\s+', summary)
            summary = " ".join(sentences[:3])
        
        return summary if summary else "Unable to generate summary from document content."
    
    def clean_text(self, text: str) -> str:
        """Enhanced text cleaning to separate merged words"""
        
        # Step 1: Fix camelCase (lowercase followed by uppercase)
        text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
        
        # Step 2: Separate common word merges - article + word patterns
        # Fix patterns like "theword" -> "the word", "andword" -> "and word"
        common_words = [
            'the', 'and', 'that', 'with', 'this', 'from', 'have', 'which',
            'their', 'would', 'could', 'should', 'there', 'where', 'these',
            'those', 'about', 'other', 'after', 'before', 'under', 'over'
        ]
        for word in common_words:
            # Pattern: word followed by lowercase letter (start of next word)
            pattern = rf'\b{word}([a-z]{{2,}})'
            text = re.sub(pattern, rf'{word} \1', text, flags=re.IGNORECASE)
        
        # Step 3: Fix merged words where one ends and another begins
        # Look for patterns like "wordAnother" or "textNew"
        text = re.sub(r'([a-z]{3,})([A-Z][a-z]{2,})', r'\1 \2', text)
        
        # Step 4: Fix "ing" + word merges (common OCR issue)
        text = re.sub(r'(ing)([a-z]{3,})', r'\1 \2', text)
        
        # Step 5: Fix "ed" + word merges
        text = re.sub(r'(ed)([a-z]{3,})', r'\1 \2', text)
        
        # Step 6: Fix "tion" + word merges
        text = re.sub(r'(tion)([a-z]{3,})', r'\1 \2', text)
        
        # Step 7: Fix number + letter boundaries
        text = re.sub(r'([0-9])([a-zA-Z])', r'\1 \2', text)
        text = re.sub(r'([a-zA-Z])([0-9])', r'\1 \2', text)
        
        # Step 8: Fix common preposition merges
        prepositions = ['for', 'are', 'can', 'but', 'not', 'all', 'one', 'may', 'has', 'was', 'been']
        for prep in prepositions:
            pattern = rf'\b{prep}([a-z]{{3,}})'
            text = re.sub(pattern, rf'{prep} \1', text, flags=re.IGNORECASE)
        
        # Step 9: Normalize whitespace
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Step 10: Fix punctuation spacing
        text = re.sub(r'\s+([.,:;!?])', r'\1', text)
        text = re.sub(r'([.!?])([A-Z])', r'\1 \2', text)
        
        return text.strip()
    
    def extract_answer(self, question: str, context: str) -> str:
        """Extract answer with smart formatting based on question type"""
        if not context or len(context.strip()) < 20:
            return "Information not available in the document."
        
        # Detect special format request
        format_type = self.detect_special_format(question)
        
        # Extract number of points if requested
        num_points = 10
        points_match = re.search(r'(\d+)\s*points?', question.lower())
        if points_match:
            num_points = int(points_match.group(1))
        
        # Handle special formats
        if format_type == 'points':
            return self.format_as_points(context, num_points)
        elif format_type == 'summary':
            return self.create_connected_response(context, question)
        
        # Default: extract coherent answer with connected flow
        return self.create_connected_response(context, question)
    
    def create_connected_response(self, context: str, question: str) -> str:
        """Create a continuous, well-connected response using preferences"""
        context = self.clean_text(context)
        
        # Split into paragraphs for better context preservation
        paragraphs = [p.strip() for p in context.split('\n\n') if p.strip() and len(p) > 50]
        
        if not paragraphs:
            return "Unable to extract meaningful content from document."
        
        # Extract quality sentences from paragraphs
        quality_sentences = []
        for para in paragraphs[:10]:  # Check first 10 paragraphs
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
            for sent in sentences:
                # Apply quality filters from preferences
                if len(sent.split()) < PREFERENCES["text_processing"]["min_words_per_sentence"]:
                    continue
                if re.match(r'^[\d\.\s\-]+$', sent):  # Skip number-only lines
                    continue
                if sent.count('.') > 5:  # Skip TOC-like entries
                    continue
                quality_sentences.append(sent)
        
        if not quality_sentences:
            return "The document contains technical information but specific details could not be extracted."
        
        # Find most relevant sentences based on question
        question_terms = set(re.findall(r'\b\w{3,}\b', question.lower()))
        scored = []
        for i, sent in enumerate(quality_sentences):
            sent_lower = sent.lower()
            relevance = sum(1 for term in question_terms if term in sent_lower)
            # Bonus for early position and good length
            position_score = max(0, 1 - (i * 0.02))
            length_score = min(len(sent.split()) / 20, 1.5)
            total = relevance * 3 + position_score + length_score
            scored.append((total, i, sent))
        
        # Sort by relevance but prefer sequential sentences for flow
        scored.sort(key=lambda x: (-x[0], x[1]))
        
        # Select top sentences, trying to keep them sequential
        if scored and scored[0][0] > 0:
            # Pick most relevant
            selected_indices = [scored[0][1]]
            
            # Try to add adjacent sentences for continuity
            for score, idx, sent in scored[1:]:
                if len(selected_indices) >= 4:
                    break
                # Prefer adjacent sentences for flow
                if any(abs(idx - si) <= 2 for si in selected_indices):
                    selected_indices.append(idx)
            
            selected_indices.sort()
            sentences_to_use = [quality_sentences[i] for i in selected_indices if i < len(quality_sentences)]
        else:
            # No relevance match, use first few good sentences
            sentences_to_use = quality_sentences[:3]
        
        # Connect sentences with transitions if needed
        if len(sentences_to_use) > 1 and PREFERENCES["output_format"]["sentence_linking"]:
            connected = []
            for i, sent in enumerate(sentences_to_use):
                if i > 0 and i < len(sentences_to_use):
                    # Check if sentences are naturally connected
                    prev_words = set(re.findall(r'\b\w{4,}\b', connected[-1].lower()))
                    curr_words = set(re.findall(r'\b\w{4,}\b', sent.lower()))
                    overlap = len(prev_words & curr_words)
                    
                    # Add transition if no natural connection
                    if overlap == 0 and len(connected) > 0:
                        transitions = PREFERENCES.get("transition_words", ["Additionally", "Furthermore"])
                        transition = transitions[i % len(transitions)]
                        sent = f"{transition}, {sent[0].lower()}{sent[1:]}"
                
                connected.append(sent)
            
            response = " ".join(connected)
        else:
            response = " ".join(sentences_to_use)
        
        # Ensure proper ending
        if response and not response.endswith(('.', '!', '?')):
            response += "."
        
        # Truncate if too long while preserving flow
        max_length = 800
        if len(response) > max_length:
            sents = re.split(r'(?<=[.!?])\s+', response)
            response = " ".join(sents[:4])
        
        return response if len(response) > 20 else "Unable to generate a coherent response from the document content."
    
    def answer_questions(self, pdf_url: str, questions: List[str]) -> List[str]:
        """Answer multiple questions about a PDF"""
        # Process PDF
        if not self.process_pdf(pdf_url):
            return ["Failed to process PDF"] * len(questions)
        
        answers = []
        print(f"\n📝 Answering {len(questions)} questions...\n")
        
        for i, question in enumerate(questions, 1):
            try:
                print(f"  Q{i}: {question[:50]}...")
                
                # Retrieve relevant chunks
                chunks = self.retrieval.retrieve(
                    question,
                    top_k=RAG_CONFIG["top_k_chunks"]
                )
                
                if not chunks:
                    answer = "Information not found in the document."
                else:
                    # Build context from chunks
                    context = "\n".join([c['text'] for c in chunks])
                    
                    # Extract answer
                    answer = self.extract_answer(question, context)
                
                answers.append(answer)
                print(f"  A{i}: {answer[:60]}..." if len(answer) > 60 else f"  A{i}: {answer}")
                
            except Exception as e:
                print(f"  ✗ Error: {e}")
                answers.append(f"Error: {str(e)}")
        
        print()
        return answers

# ============================================================================
# FASTAPI SERVER
# ============================================================================

app = FastAPI(title="AI Battle Arena - Offline RAG")

# Add CORS middleware to allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

# Global RAG
rag_pipeline = RAGPipeline()

# Store last results for display
last_results = {
    "pdf_url": None,
    "questions": [],
    "answers": [],
    "timestamp": None,
    "status": "No requests processed yet"
}

# Models
class QuestionRequest(BaseModel):
    pdf_url: str
    questions: List[str]

class AnswerResponse(BaseModel):
    success: bool
    answers: List[str]
    message: str

# Routes
@app.get("/")
async def root():
    """Serve HTML interface"""
    if os.path.exists("rag_frontend.html"):
        return FileResponse("rag_frontend.html", media_type="text/html")
    return {"message": "AI Battle Arena - RAG System", "status": "ready"}

@app.get("/favicon.ico")
async def favicon():
    """Serve favicon to prevent 404 errors"""
    favicon_path = "favicon.ico"
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/x-icon")
    # Return empty response if favicon doesn't exist
    from fastapi.responses import Response
    return Response(content=b"", media_type="image/x-icon")

@app.get("/health")
async def health():
    """Health check"""
    return {
        "status": "healthy",
        "system": "AI Battle Arena RAG",
        "mode": "text-based retrieval"
    }

@app.get("/aibattle")
async def aibattle_info():
    """Show last processed results in JSON format"""
    if last_results["answers"]:
        # Return results as JSON
        return {
            "pdf_url": last_results["pdf_url"],
            "questions": last_results["questions"],
            "answers": last_results["answers"],
            "timestamp": last_results["timestamp"],
            "status": "success"
        }
    else:
        # No results yet - show API info
        return {
            "status": last_results["status"],
            "endpoint": "/aibattle",
            "method": "POST",
            "description": "Answer questions from PDF documents",
            "input_format": {
                "pdf_url": "string (URL to PDF)",
                "questions": ["array", "of", "questions"]
            },
            "output_format": {
                "answers": ["array", "of", "answers"]
            },
            "usage": "Visit http://localhost:8000 to use the web interface"
        }

@app.post("/aibattle")
async def answer(request: QuestionRequest):
    """Answer questions endpoint"""
    from datetime import datetime
    try:
        print(f"\n🔄 Received request: {len(request.questions)} question(s)")
        print(f"   PDF: {request.pdf_url[:60]}...")
        
        if not request.pdf_url or not request.questions:
            raise HTTPException(status_code=400, detail="Missing PDF URL or questions")
        
        answers = rag_pipeline.answer_questions(request.pdf_url, request.questions)
        
        # Store results for GET endpoint display
        last_results["pdf_url"] = request.pdf_url
        last_results["questions"] = request.questions
        last_results["answers"] = answers
        last_results["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        last_results["status"] = "success"
        
        print(f"✅ Request completed: {len(answers)} answer(s) returned")
        print(f"   View results at: http://localhost:8000/aibattle")
        
        # Return only answers array as per specification
        return {
            "answers": answers
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Error processing request: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*70)
    print(" 🤖 AI BATTLE ARENA - OFFLINE RAG SYSTEM")
    print("="*70)
    print(" Mode: Text-based retrieval with OCR support")
    print(" Features: PDF download, text extraction, OCR, intelligent answer extraction")
    if TESSERACT_AVAILABLE:
        print(" ✓ OCR/Tesseract: AVAILABLE")
    else:
        print(" ℹ OCR/Tesseract: Not installed (text-based extraction only)")
    print("="*70)
    
    import os
    port = int(os.environ.get("PORT", 8000))
    print(f"\n 🌐 Server: http://localhost:{port}")
    print(f" 📖 Frontend: http://localhost:{port}/")
    print(f" 💚 Health: http://localhost:{port}/health\n")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
