"""Chunking intelligente del testo per invio al LLM."""

import tiktoken


class TextChunker:
    """Divide il testo in chunk di dimensione controllata (in token)."""

    def __init__(self, max_tokens: int = 2000, overlap_tokens: int = 200):
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self._enc = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(self, text: str) -> int:
        return len(self._enc.encode(text))

    def _encode(self, text: str) -> list[int]:
        return self._enc.encode(text)

    def _decode(self, tokens: list[int]) -> str:
        return self._enc.decode(tokens)

    def split(self, text: str) -> list[str]:
        """Divide il testo in chunk rispettando i limiti di token.

        1. Divide per paragrafi (\\n\\n).
        2. Accumula paragrafi finché non supera max_tokens.
        3. Se un paragrafo singolo supera max_tokens, lo spezza per frasi.
        4. Applica overlap tra chunk consecutivi.
        """
        paragraphs = text.replace("\f", "\n\n").split("\n\n")
        chunks = []
        current_parts = []
        current_tokens = 0

        for para in paragraphs:
            para_tokens = self._count_tokens(para)

            if para_tokens > self.max_tokens:
                # Chiudi chunk corrente se non vuoto
                if current_parts:
                    chunks.append("\n\n".join(current_parts))
                    current_parts = []
                    current_tokens = 0

                # Spezza il paragrafo lungo per frasi
                sentence_chunks = self._split_long_paragraph(para)
                chunks.extend(sentence_chunks)
                continue

            if current_tokens + para_tokens > self.max_tokens and current_parts:
                # Chiudi chunk corrente
                chunks.append("\n\n".join(current_parts))

                # Calcola overlap: prendi gli ultimi overlap_tokens dal chunk appena chiuso
                if self.overlap_tokens > 0:
                    overlap_text = self._get_overlap(chunks[-1])
                    current_parts = [overlap_text, para]
                    current_tokens = self._count_tokens(overlap_text) + para_tokens
                else:
                    current_parts = [para]
                    current_tokens = para_tokens
            else:
                current_parts.append(para)
                current_tokens += para_tokens

        # Ultimo chunk
        if current_parts:
            chunks.append("\n\n".join(current_parts))

        return chunks if chunks else [text]

    def _split_long_paragraph(self, para: str) -> list[str]:
        """Spezza un paragrafo lungo per frasi."""
        sentences = para.replace(". ", ".\n").split("\n")
        chunks = []
        current_parts = []
        current_tokens = 0

        for sentence in sentences:
            s_tokens = self._count_tokens(sentence)

            if s_tokens > self.max_tokens:
                # Frase lunghissima: spezza a forza per token
                if current_parts:
                    chunks.append(" ".join(current_parts))
                    current_parts = []
                    current_tokens = 0
                tokens = self._encode(sentence)
                for i in range(0, len(tokens), self.max_tokens):
                    chunk_tokens = tokens[i:i + self.max_tokens]
                    chunks.append(self._decode(chunk_tokens))
                continue

            if current_tokens + s_tokens > self.max_tokens and current_parts:
                chunks.append(" ".join(current_parts))
                current_parts = [sentence]
                current_tokens = s_tokens
            else:
                current_parts.append(sentence)
                current_tokens += s_tokens

        if current_parts:
            chunks.append(" ".join(current_parts))

        return chunks

    def _get_overlap(self, text: str) -> str:
        """Restituisce gli ultimi overlap_tokens token del testo."""
        tokens = self._encode(text)
        if len(tokens) <= self.overlap_tokens:
            return text
        return self._decode(tokens[-self.overlap_tokens:])
