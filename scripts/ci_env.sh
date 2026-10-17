#!/bin/sh
# Writes CI's .env: the dev placeholders of .env.example without the values a job must set itself.
# The Makefile's -include .env beats the job environment, so a provider key line, even empty, would
# hide the job's key, and FASTEMBED_CACHE_PATH would keep the Windows path. Model calls stay off
# unless a step passes AI_ENABLED=true to make itself.
set -eu
sed -E '/^((GEMINI|NVIDIA|OPENROUTER|ANTHROPIC)_API_KEY|FASTEMBED_CACHE_PATH|AI_ENABLED|LLM_FALLBACK_ON_RATE_LIMIT|LLM_CLAUDE_FALLBACK)=/d' \
	.env.example >.env
{
	echo "AI_ENABLED=false"
	echo "LLM_FALLBACK_ON_RATE_LIMIT=false"
	echo "LLM_CLAUDE_FALLBACK=false"
	if [ -n "${FASTEMBED_CACHE_PATH:-}" ]; then
		echo "FASTEMBED_CACHE_PATH=$FASTEMBED_CACHE_PATH"
	fi
} >>.env
