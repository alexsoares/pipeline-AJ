"""Hierarquia de erros da pipeline. Cada passo levanta uma subclasse de PipelineError."""


class PipelineError(Exception):
    """Erro base: interrompe a pipeline com uma mensagem legível para o chat."""


class MissingInputError(PipelineError):
    """Dados imprescindíveis (repositório e/ou card) ausentes ou inválidos."""


class ConfigError(PipelineError):
    """Arquivo de configuração ausente ou malformado."""


class GitError(PipelineError):
    """Falha ao executar ou interpretar um comando Git."""


class ClassificationError(PipelineError):
    """O LLM não retornou uma classificação válida."""


class ClaudeExecutionError(PipelineError):
    """Falha na execução do Claude Code."""


class DocumentationError(PipelineError):
    """Falha ao gerar o documento Markdown do card."""
