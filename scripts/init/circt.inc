# CIRCT installation targets
# Simplified logic: skip if installed, always build from source, install from wheel if available

.PHONY: install-circt clean-circt patch-circt build-pycde-source verify-pycde-installation check-wheel-exists install-pycde-wheel

# Patch target for circt
patch-circt: 3rd-party/circt/.patch-applied

# Marker file to track patch application
3rd-party/circt/.patch-applied:
	@echo "Checking circt patch status..."
	@cd 3rd-party/circt && \
		if bash ../../scripts/patch-apply.sh check ../../scripts/init/patches/circt.patch 2>/dev/null; then \
			echo "CIRCT patch already applied — skipping patch step."; \
		else \
			echo "Applying CIRCT patch..."; \
			bash ../../scripts/patch-apply.sh apply ../../scripts/init/patches/circt.patch; \
		fi && \
		touch $(CURDIR)/3rd-party/circt/.patch-applied

install-circt: install-py-package 3rd-party/circt/.circt-built

# Build PyCDE from source
build-pycde-source:
	@echo "Building PyCDE from source..."
	@cd 3rd-party/circt/frontends/PyCDE && \
	CIRCT_DIRECTORY="$(CURDIR)/3rd-party/circt" \
	CIRCT_EXTRA_CMAKE_ARGS="-DESI_RUNTIME=OFF -DZ3_DISABLE=ON -DOR_TOOLS_DISABLE=ON -DLLVM_FORCE_VC_REPOSITORY=https://github.com/llvm/llvm-project.git" \
	SETUPTOOLS_SCM_PRETEND_VERSION="0.0.0" \
	SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYCDE="0.0.0" \
	../../../../$(VENV_DIR)/bin/python -m build
	@echo "PyCDE built successfully from source."

# Verify PyCDE installation
verify-pycde-installation:
	@echo "Verifying PyCDE installation..."
	@$(VENV_DIR)/bin/python -c "import pycde; print('PyCDE version:', getattr(pycde, '__version__', 'unknown'))"
	@echo "CIRCT installation completed successfully."

# Check if wheel exists
check-wheel-exists:
	@if [ ! -d "3rd-party/circt/frontends/PyCDE/dist" ] || [ -z "$$(ls 3rd-party/circt/frontends/PyCDE/dist/*.whl 2>/dev/null)" ]; then \
		echo "No wheel found, building PyCDE from source..."; \
		$(MAKE) build-pycde-source; \
	fi

# Install PyCDE from wheel
install-pycde-wheel:
	@echo "Installing PyCDE from wheel..."
	@$(VENV_DIR)/bin/pip install 3rd-party/circt/frontends/PyCDE/dist/*.whl

# Marker file to track build completion
3rd-party/circt/.circt-built: patch-circt
	@echo "Installing PyCDE..."
	@# Skip if already installed
	@if $(VENV_DIR)/bin/python -c "import pycde" 2>/dev/null; then \
		echo "PyCDE is already installed — skipping installation."; \
		touch $(CURDIR)/3rd-party/circt/.circt-built; \
	else \
		echo "Installing PyCDE..."; \
		echo "Step 1: Checking if PyCDE is already installed..."; \
		if . .assassyn-venv/bin/activate && python -c "import pycde" 2>/dev/null; then \
			echo "PyCDE is already installed — skipping installation."; \
		else \
			echo "PyCDE not found. Proceeding with installation..."; \
			echo "Step 2: Attempting to install PyCDE via pip..."; \
			if . .assassyn-venv/bin/activate && pip install pycde 2>/dev/null; then \
				echo "PyCDE installed successfully via pip."; \
			else \
				echo "Failed to install PyCDE via pip. Proceeding to source installation..."; \
				echo "Step 3: Building PyCDE from source..."; \
				if ! (cd 3rd-party/circt/frontends/PyCDE && \
					CIRCT_DIRECTORY="$$(pwd)/../../" CIRCT_EXTRA_CMAKE_ARGS="-DESI_RUNTIME=OFF -DZ3_DISABLE=ON -DOR_TOOLS_DISABLE=ON" \
					. $$(pwd)/../../../../.assassyn-venv/bin/activate && python -m build); then \
					echo "ERROR: Failed to build PyCDE from source."; \
					exit 1; \
				fi; \
				echo "Installing PyCDE wheel..."; \
				if ! (cd 3rd-party/circt/frontends/PyCDE && \
					. $$(pwd)/../../../../.assassyn-venv/bin/activate && pip install --force-reinstall ./dist/*.whl); then \
					echo "ERROR: Failed to install PyCDE wheel."; \
					exit 1; \
				fi; \
				echo "PyCDE built and installed successfully from source."; \
			fi; \
		fi; \
		echo "Step 4: Verifying PyCDE installation..."; \
		if ! (. .assassyn-venv/bin/activate && python -c "import pycde; print('PyCDE version:', getattr(pycde, '__version__', 'unknown'))"); then \
			echo "ERROR: PyCDE installation verification failed. pycde module cannot be imported."; \
			exit 1; \
		fi; \
		echo "CIRCT installation completed successfully."; \
		touch $(CURDIR)/$@; \
	fi

clean-circt:
	@echo "Cleaning CIRCT build artifacts and uninstalling PyCDE..."
	@if [ -d "$(VENV_DIR)" ]; then \
		$(VENV_DIR)/bin/pip uninstall -y pycde 2>/dev/null || true; \
	fi
	rm -rf 3rd-party/circt/frontends/PyCDE/dist 3rd-party/circt/frontends/PyCDE/*.egg-info
	@bash scripts/patch-apply.sh reverse scripts/init/patches/circt.patch 2>/dev/null || true
	rm -f 3rd-party/circt/.patch-applied 3rd-party/circt/.circt-built
	@echo "CIRCT clean completed."
