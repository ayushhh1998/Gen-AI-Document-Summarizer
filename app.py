# -*- coding: utf-8 -*-
"""Untitled1.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1-z2QK93hWCy4HjDeYEtC5OB1TBVBlR5R
"""

!pip install flask-ngrok langchain langchain-community faiss-cpu tiktoken "unstructured[pdf]" --quiet
!pip install pyngrok

import os, logging
from transformers import AutoTokenizer, pipeline
from unstructured.partition.auto import partition
from langchain.text_splitter import RecursiveCharacterTextSplitter, TokenTextSplitter
from langchain.schema import Document
from langchain.prompts import PromptTemplate
from langchain.chains import load_summarize_chain, RetrievalQA
from langchain_community.llms import HuggingFacePipeline
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.vectorstores import FAISS
import re

logging.getLogger().handlers.clear()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ─── Model & Tokenizer Setup ─────────────────────────────────────────────────
os.environ["HUGGINGFACEHUB_API_TOKEN"] = "hf_XXXXXX"
MODEL = "ibm-granite/granite-3.3-2b-instruct"

tokenizer = AutoTokenizer.from_pretrained(MODEL)

pipe = pipeline(
    "text-generation",
    model=MODEL,
    tokenizer=tokenizer,
    device_map="auto",
    max_new_tokens=512,
    do_sample=True,
    temperature=0.3
)

llm = HuggingFacePipeline(pipeline=pipe)
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")

# ─── Utility Functions ────────────────────────────────────────────────────────
def process_document(path: str) -> str:
    parts = []
    for el in partition(filename=path):
        parts.append(f"TABLE:\n{el.text}" if el.category=="Table" else el.text)
    return "\n\n".join(parts)

def create_knowledge_base(text):
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100)
    chunks = splitter.split_text(text)
    return FAISS.from_texts(chunks, embeddings)

def get_qa_chain(vs):
    retr = vs.as_retriever(search_kwargs={"k":2})
    prompt = PromptTemplate(
        input_variables=["context","question"],
        template="""<|system|>
You are a banking expert. Use ONLY the context. If you do not know anything, say "I don't know." Do not invent information and avoid hallucination.
<|user|>
Context: {context}

Question: {question}
<|assistant|>
 """
    )
    return RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=retr,
        chain_type_kwargs={"prompt":prompt},
        return_source_documents=False
    )

def is_summary_request(prompt):
    keywords = ["summary", "summarize", "overview", "give summary", "brief"]
    return any(k in prompt.lower() for k in keywords)

def get_summary(text: str) -> str:
    splitter = TokenTextSplitter(chunk_size=500, chunk_overlap=10)
    chunks = splitter.split_text(text)
    docs = [Document(page_content=c) for c in chunks]

    map_prompt = PromptTemplate(
        input_variables=["text"],
        template="""
<|system|> You are a concise assistant for banking docs. <|user|> Summarize in short only, no hallucinations: {text} <|assistant|> """ )

    combine_prompt = PromptTemplate(
        input_variables=["text"],
        template="""<|system|> You are a banking reports summarization expert. <|user|> Combine chunk summaries into one concise summary, strictly factual: {text} <|assistant|> """ )

    chain = load_summarize_chain(
        llm=llm,
        chain_type="map_reduce",
        map_prompt=map_prompt,
        combine_prompt=combine_prompt,
        token_max=1024,
        verbose=False,
        return_intermediate_steps=False
    )
    result = chain.invoke(docs)
    raw_output = result["output_text"]

    # Clean output: remove everything before the final actual answer
    # This regex keeps only the last assistant response (after the last <|assistant|>)
    cleaned_output = re.split(r"<\|assistant\|>", raw_output)[-1].strip()

    return cleaned_output

# ─── Ngrok Setup ──────────────────────────────────────────────────────────────
!ngrok config add-authtoken 2wXXXXXXX

from flask import Flask, request, render_template_string, url_for
from pyngrok import ngrok
import os, re

app = Flask(__name__)
UPLOAD_FOLDER = "/content/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─── Global State ─────────────────────────────────────────────────────────────
text_data = ""
vector_store = None
current_filename = None

# ─── HTML Template ────────────────────────────────────────────────────────────
html_template = '''
<!doctype html>
<html>
<head>
  <title>Gen-AI Document Summarizer</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 0; padding: 0;
      background-color: #f7f7f7;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: center;
      background-color: maroon;
      color: white;
      padding: 15px;
    }
    h1 {
      font-size: 2.8em;
      margin: 0;
    }
    .container {
      display: flex;
      flex-direction: row;
      padding: 40px;
    }
    .left-column, .right-column {
      width: 50%;
      padding: 20px;
    }
    .left-column {
      border-right: 2px solid #ccc;
    }
    textarea {
      width: 100%;
      height: 100px;
      font-size: 1em;
      padding: 10px;
      border-radius: 10px;
    }
    .file-upload-wrapper {
      margin-top: 20px;
    }
    input[type="file"] {
      margin-bottom: 10px;
    }
    .submit-button {
      background-color: maroon;
      color: white;
      border: none;
      padding: 10px 16px;
      font-size: 1.2em;
      border-radius: 8px;
      cursor: pointer;
      margin-top: 10px;
    }
    .submit-button:hover {
      background-color: darkred;
    }
    .response-box {
      background-color: white;
      padding: 20px;
      border-radius: 15px;
      box-shadow: 0 0 10px rgba(0,0,0,0.1);
    }
    .progress-ring {
      margin-top: 10px;
      width: 100px;
      height: 100px;
      position: relative;
    }
    .progress-ring-circle {
      stroke: #4CAF50;
      stroke-width: 10;
      fill: transparent;
      transform: rotate(-90deg);
      transform-origin: 50% 50%;
    }
    .progress-ring-text {
      position: absolute;
      top: 35px;
      left: 35px;
      font-size: 18px;
      font-weight: bold;
    }
    .file-name-display {
      font-weight: bold;
      color: #444;
      margin-top: 10px;
    }
  </style>
</head>
<body>

<header>
  <h1>Gen-AI Document Summarizer</h1>
</header>

<div class="container">
  <div class="left-column">
    <form method="post" enctype="multipart/form-data" id="form">
      <textarea name="query" placeholder="Type your question or 'summarize'..." required>{{ query or '' }}</textarea>
      {% if filename %}
      <div class="file-name-display">📄 Using Document: {{ filename }}</div>
      {% endif %}
      <div class="file-upload-wrapper">
        <input type="file" name="document" onchange="updateProgress(); document.getElementById('form').submit();">
        <div class="progress-ring" id="progress-ring" style="display:none;">
          <svg width="100" height="100">
            <circle r="40" cx="50" cy="50" stroke="#ddd" stroke-width="10" fill="transparent"/>
            <circle id="progress-ring-circle" class="progress-ring-circle" r="40" cx="50" cy="50" />
          </svg>
          <div class="progress-ring-text" id="progress-ring-text">0%</div>
        </div>
      </div>
      <button class="submit-button" type="submit">&#10148;</button>
    </form>
  </div>

  <div class="right-column">
    {% if result %}
    <div class="response-box">
      <h3>Answer:</h3>
      <p>{{ result }}</p>
    </div>
    {% endif %}
  </div>
</div>

<script>
  function updateProgress() {
    const ring = document.getElementById("progress-ring");
    const circle = document.getElementById("progress-ring-circle");
    const text = document.getElementById("progress-ring-text");
    ring.style.display = "block";

    let percent = 0;
    let radius = circle.r.baseVal.value;
    let circumference = 2 * Math.PI * radius;

    circle.style.strokeDasharray = `${circumference} ${circumference}`;
    circle.style.strokeDashoffset = circumference;

    let interval = setInterval(() => {
      if (percent >= 100) {
        clearInterval(interval);
        return;
      }
      percent += 5;
      let offset = circumference - (percent / 100) * circumference;
      circle.style.strokeDashoffset = offset;
      text.textContent = `${percent}%`;
    }, 80);
  }
</script>

</body>
</html>
'''

# ─── Flask Route ──────────────────────────────────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def upload_file():
    global text_data, vector_store, current_filename
    result = None
    query = ""

    if request.method == "POST":
        uploaded_file = request.files.get("document")
        query = request.form.get("query", "")

        # Handle new file upload
        if uploaded_file and uploaded_file.filename != "":
            file_path = os.path.join(UPLOAD_FOLDER, uploaded_file.filename)
            uploaded_file.save(file_path)
            current_filename = uploaded_file.filename
            text_data = process_document(file_path)
            vector_store = create_knowledge_base(text_data)

        # Handle user query
        if text_data and query:
            if is_summary_request(query):
                result = get_summary(text_data)
            else:
                if vector_store:
                    qa_chain = get_qa_chain(vector_store)
                    raw_output = qa_chain.run(query)
                    result = re.split(r"<\|assistant\|>", raw_output)[-1].strip()
                else:
                    result = "⚠️ Please upload a document first."

    return render_template_string(
        html_template,
        result=result,
        query=query,
        filename=current_filename,
        url_for=url_for
    )

# ─── Start App with Ngrok ─────────────────────────────────────────────────────
public_url = ngrok.connect(5000)
print(" 🔗 Your app is live at:", public_url)
app.run(port=5000)