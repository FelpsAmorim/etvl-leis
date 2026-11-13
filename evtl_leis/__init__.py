"""evtl-leis."""

# Usa o trust store do sistema operacional nas conexões TLS (o bundle do Python
# não valida a cadeia servida por alguns sites de prefeitura).
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass
