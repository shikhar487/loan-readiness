# HuggingFace Docker Space for the Streamlit portal.
# Docker is used (rather than the Streamlit SDK) because the Spaces create-API no
# longer accepts "streamlit" as an sdk option. This image is fully self-contained.
FROM python:3.11-slim

# Run as the non-root user HuggingFace expects (UID 1000).
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

EXPOSE 7860
CMD ["streamlit", "run", "app.py", \
     "--server.port=7860", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
