import SwiftUI
import CompanionCore

// MARK: - Backward-compatible Symbol Animation Helpers

struct AnimatedMascotView: View {
    let mascotState: MascotState
    let size: CGFloat

    var body: some View {
        ZStack {
            Circle()
                .fill(mascotState.attentionColor.opacity(0.15))
                .frame(width: size, height: size)

            Image(systemName: mascotState.symbolName)
                .font(.system(size: size * 0.5, weight: .medium))
                .foregroundStyle(mascotState.attentionColor)
                .modifier(MascotAnimationModifier(animationType: mascotState.animationType, isActive: mascotState.isAnimated))
        }
    }
}

private struct MascotAnimationModifier: ViewModifier {
    let animationType: MascotAnimation
    let isActive: Bool

    @State private var rotationAngle: Double = 0
    @State private var bounceScale: CGFloat = 1.0
    @State private var pulseScale: CGFloat = 1.0
    @State private var previousAnimationType: MascotAnimation = .none
    @State private var previousIsActive: Bool = false

    func body(content: Content) -> some View {
        content
            .rotationEffect(.degrees(rotationAngle))
            .scaleEffect(bounceScale * pulseScale)
            .onAppear {
                previousAnimationType = animationType
                previousIsActive = isActive
                if isActive { startAnimation() }
            }
            .onChange(of: isActive) { newValue in
                if newValue != previousIsActive {
                    previousIsActive = newValue
                    if newValue { startAnimation() } else { stopAnimation() }
                }
            }
            .onChange(of: animationType) { newValue in
                if newValue != previousAnimationType {
                    previousAnimationType = newValue
                    if isActive { stopAnimation(); startAnimation() }
                }
            }
    }

    private func startAnimation() {
        switch animationType {
        case .rotate:
            withAnimation(.linear(duration: 2).repeatForever(autoreverses: false)) {
                rotationAngle = 360
            }
        case .bounce:
            withAnimation(.spring(response: 0.5, dampingFraction: 0.5).repeatForever(autoreverses: true)) {
                bounceScale = 1.2
            }
        case .pulse:
            withAnimation(.easeInOut(duration: 1).repeatForever(autoreverses: true)) {
                pulseScale = 1.15
            }
        case .none:
            break
        }
    }

    private func stopAnimation() {
        rotationAngle = 0
        bounceScale = 1.0
        pulseScale = 1.0
    }
}

struct AnimatedStatusDotView: View {
    let status: AgentStatus
    let size: CGFloat

    var body: some View {
        ZStack {
            Circle()
                .fill(status.color)
                .frame(width: size, height: size)

            if status == .working || status == .thinking {
                Circle()
                    .stroke(status.color, lineWidth: 1.5)
                    .frame(width: size * 1.75, height: size * 1.75)
                    .modifier(PulseAnimationModifier(isActive: true))
            }
        }
    }
}

private struct PulseAnimationModifier: ViewModifier {
    let isActive: Bool
    @State private var scale: CGFloat = 1.0
    @State private var opacity: Double = 1.0
    @State private var previousIsActive: Bool = false

    func body(content: Content) -> some View {
        content
            .scaleEffect(scale)
            .opacity(opacity)
            .onAppear {
                previousIsActive = isActive
                if isActive {
                    withAnimation(.easeInOut(duration: 1.5).repeatForever(autoreverses: true)) {
                        scale = 1.5
                        opacity = 0.3
                    }
                }
            }
            .onChange(of: isActive) { newValue in
                if newValue != previousIsActive {
                    previousIsActive = newValue
                    if newValue {
                        withAnimation(.easeInOut(duration: 1.5).repeatForever(autoreverses: true)) {
                            scale = 1.5
                            opacity = 0.3
                        }
                    } else {
                        scale = 1.0
                        opacity = 1.0
                    }
                }
            }
    }
}

// MARK: - Main Panel View

struct CompanionPanelView: View {
    @EnvironmentObject var viewModel: PanelViewModel
    @State private var hoverPill = false

    var body: some View {
        ZStack(alignment: .top) {
            if viewModel.isServerRunning, let state = viewModel.state {
                if viewModel.isExpanded {
                    ExpandedPanelView(state: state)
                        .transition(.asymmetric(
                            insertion: .move(edge: .top).combined(with: .opacity),
                            removal: .move(edge: .top).combined(with: .opacity)
                        ))
                        .zIndex(1)
                } else {
                    CollapsedPillView(state: state, hoverPill: $hoverPill)
                        .transition(.asymmetric(
                            insertion: .opacity.combined(with: .scale(scale: 0.95)),
                            removal: .opacity.combined(with: .scale(scale: 1.05))
                        ))
                        .zIndex(1)
                }
            } else {
                NotRunningView()
                    .transition(.opacity)
            }
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.8), value: viewModel.isExpanded)
        .animation(.spring(response: 0.3, dampingFraction: 0.8), value: viewModel.isServerRunning)
        .frame(width: 360, height: viewModel.isExpanded ? nil : 48, alignment: .top)
        .fixedSize(horizontal: false, vertical: true)
        .onHover { inside in
            if !viewModel.isExpanded {
                hoverPill = inside
            }
        }
        .onTapGesture {
            if !viewModel.isExpanded {
                viewModel.expandTemporarily()
            }
        }
        .dropDestination(for: URL.self) { urls, _ in
            guard let url = urls.first else { return false }
            Task { await viewModel.dropFile(url: url) }
            return true
        } isTargeted: { _ in }
    }
}

// MARK: - Collapsed Pill

struct CollapsedPillView: View {
    let state: CompanionState
    @Binding var hoverPill: Bool
    @EnvironmentObject var viewModel: PanelViewModel

    private var mascotState: MascotState {
        MascotState(mood: state.mood)
    }

    private var statusText: String {
        if !state.requests.isEmpty {
            return "\(state.requests.count) request\(state.requests.count == 1 ? "" : "s") waiting"
        }
        let workingCount = state.agents.filter { $0.status == .working || $0.status == .thinking }.count
        if workingCount > 0 {
            return "\(workingCount) agent\(workingCount == 1 ? "" : "s") working"
        }
        if state.mood == .done {
            return "All done"
        }
        return "Idle"
    }

    var body: some View {
        HStack(spacing: 10) {
            // Mascot
            AnimatedMascotView(mascotState: mascotState, size: 32)
                .scaleEffect(hoverPill ? 1.1 : 1.0)
                .animation(.spring(response: 0.2, dampingFraction: 0.6), value: hoverPill)

            // Status text
            VStack(alignment: .leading, spacing: 1) {
                Text("Rooks")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.primary)
                Text(statusText)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer()

            // Chevron indicator
            Image(systemName: "chevron.down")
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(.tertiary)
                .rotationEffect(.degrees(viewModel.isExpanded ? 180 : 0))
                .animation(.spring(response: 0.3), value: viewModel.isExpanded)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .frame(width: 360, height: 48)
        .background(
            RoundedRectangle(cornerRadius: 24)
                .fill(.ultraThinMaterial)
                .shadow(color: .black.opacity(0.1), radius: 8, x: 0, y: 2)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 24)
                .stroke(.white.opacity(0.2), lineWidth: 0.5)
        )
    }
}

// MARK: - Not Running View

struct NotRunningView: View {
    @EnvironmentObject var viewModel: PanelViewModel

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "circle.dotted")
                .font(.system(size: 24))
                .foregroundStyle(.secondary)

            Text("Rooks isn't running")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(.primary)

            Text(viewModel.errorMessage ?? "Start it from the menu or run `ai-workspace`")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 20)

            Button(action: { viewModel.startServer() }) {
                Label("Start Rooks", systemImage: "play.fill")
                    .font(.system(size: 13, weight: .medium))
                    .padding(.horizontal, 20)
                    .padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.regular)
        }
        .padding(.vertical, 20)
        .frame(width: 360)
        .background(
            RoundedRectangle(cornerRadius: 16)
                .fill(.ultraThinMaterial)
                .shadow(color: .black.opacity(0.1), radius: 8, x: 0, y: 2)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(.white.opacity(0.2), lineWidth: 0.5)
        )
    }
}

// MARK: - Expanded Panel

struct ExpandedPanelView: View {
    let state: CompanionState
    @EnvironmentObject var viewModel: PanelViewModel

    var body: some View {
        VStack(spacing: 0) {
            // Header with usage
            UsageHeaderView(usage: state.usage)

            Divider().opacity(0.3)

            // Agents
            if !state.agents.isEmpty {
                AgentsSectionView(agents: state.agents)
                Divider().opacity(0.3)
            }

            // Requests
            if !state.requests.isEmpty {
                RequestsSectionView(requests: state.requests)
                Divider().opacity(0.3)
            }

            // Footer with collapse hint
            HStack {
                Spacer()
                Text("Press Esc to collapse • Return to allow top request")
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
                Spacer()
            }
            .padding(.vertical, 8)
        }
        .background(
            RoundedRectangle(cornerRadius: 16)
                .fill(.ultraThinMaterial)
                .shadow(color: .black.opacity(0.15), radius: 16, x: 0, y: 4)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(.white.opacity(0.2), lineWidth: 0.5)
        )
        .frame(width: 360)
        .fixedSize(horizontal: false, vertical: true)
    }
}

// MARK: - Usage Header

struct UsageHeaderView: View {
    let usage: Usage

    var body: some View {
        VStack(spacing: 10) {
            // Rooks month cost
            if let month = usage.rooksMonth {
                HStack {
                    Image(systemName: "dollarsign.circle.fill")
                        .foregroundStyle(.green)
                    Text("This month: \(month.formattedCost)")
                        .font(.system(size: 13, weight: .medium))
                    Spacer()
                    Text("\(month.tokens.formatted()) tokens")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
            }

            // Agent usage bars
            if !usage.agents.isEmpty {
                VStack(spacing: 6) {
                    ForEach(usage.agents) { agentUsage in
                        AgentUsageBarView(usage: agentUsage)
                    }
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
}

struct AgentUsageBarView: View {
    let usage: AgentUsageSummary

    var body: some View {
        VStack(spacing: 3) {
            HStack {
                Text(usage.name)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                Spacer()
                Text(usage.formattedUsage)
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(.quaternary)
                        .frame(height: 4)
                    RoundedRectangle(cornerRadius: 2)
                        .fill(usageColor(for: usage.progress))
                        .frame(width: geo.size.width * usage.progress, height: 4)
                        .animation(.spring(response: 0.5), value: usage.progress)
                }
            }
            .frame(height: 4)
        }
    }

    private func usageColor(for progress: Double) -> Color {
        if progress >= 0.9 { return .red }
        if progress >= 0.7 { return .orange }
        return .blue
    }
}

// MARK: - Agents Section

struct AgentsSectionView: View {
    let agents: [Agent]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Agents")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 16)

            ForEach(agents) { agent in
                AgentRowView(agent: agent)
            }
        }
        .padding(.vertical, 8)
    }
}

struct AgentRowView: View {
    let agent: Agent

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                // Status dot
                AnimatedStatusDotView(status: agent.status, size: 8)

                // Tool icon and name
                Image(systemName: toolIcon(for: agent.tool))
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                Text(agent.name)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.primary)
                    .lineLimit(1)

                Spacer()

                // Status and time
                Text(agent.status.displayName)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(agent.status.color)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(agent.status.color.opacity(0.15))
                    .clipShape(Capsule())

                Text(agent.updatedAt.relativeTimeString)
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
            }

            // Last ~6 steps
            if !agent.steps.isEmpty {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(agent.steps.suffix(6)) { step in
                        StepRowView(step: step)
                    }
                }
                .padding(.leading, 16)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(.quaternary.opacity(0.3))
        )
        .padding(.horizontal, 12)
    }

    private func toolIcon(for tool: String) -> String {
        switch tool {
        case "claude-code": return "terminal.fill"
        case "codex": return "chevron.left.forwardslash.chevron.right"
        case "gemini-cli": return "sparkles"
        case "cursor": return "cursorarrow.rays"
        case "rooks": return "circle.dotted"
        default: return "gearshape.fill"
        }
    }
}

struct StepRowView: View {
    let step: Step

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: step.kind.symbolName)
                .font(.system(size: 9))
                .foregroundStyle(step.kind.color)
                .frame(width: 12)

            Text(step.text)
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
                .lineLimit(1)

            Spacer()

            Text(step.t.relativeTimeString)
                .font(.system(size: 9))
                .foregroundStyle(.tertiary)
        }
    }
}

// MARK: - Requests Section

struct RequestsSectionView: View {
    let requests: [CompanionRequest]
    @EnvironmentObject var viewModel: PanelViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Waiting for you")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 16)

            ForEach(requests) { request in
                RequestCardView(request: request)
            }
        }
        .padding(.vertical, 8)
    }
}

struct RequestCardView: View {
    let request: CompanionRequest
    @EnvironmentObject var viewModel: PanelViewModel
    @State private var answerText = ""
    @FocusState private var isAnswerFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Header
            HStack(spacing: 8) {
                Image(systemName: request.kind == .permission ? "lock.shield" : "questionmark.circle")
                    .font(.system(size: 14))
                    .foregroundStyle(request.kind == .permission ? .orange : .blue)

                VStack(alignment: .leading, spacing: 1) {
                    Text(request.title)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(2)
                    if !request.detail.isEmpty {
                        Text(request.detail)
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                }

                Spacer()

                Text(request.agent)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(.tertiary)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.quaternary)
                    .clipShape(Capsule())
            }

            // Action/Target
            if !request.action.isEmpty || !request.target.isEmpty {
                HStack(spacing: 6) {
                    if !request.action.isEmpty {
                        Text(request.action)
                            .font(.system(size: 10, weight: .medium, design: .monospaced))
                            .foregroundStyle(.primary)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(.quaternary)
                            .clipShape(RoundedRectangle(cornerRadius: 4))
                    }
                    if !request.target.isEmpty {
                        Text(request.target)
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                }
            }

            // Options for questions
            if request.kind == .question && !request.options.isEmpty {
                VStack(spacing: 6) {
                    ForEach(request.options, id: \.self) { option in
                        Button(action: {
                            Task { await viewModel.decideRequest(request, decision: .answer, answer: option) }
                        }) {
                            HStack {
                                Text(option)
                                    .font(.system(size: 12))
                                    .foregroundStyle(.primary)
                                Spacer()
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(.quaternary.opacity(0.5))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        .buttonStyle(.plain)
                    }

                    // Custom answer field
                    if request.multi || request.options.isEmpty {
                        HStack {
                            TextField("Type your answer…", text: $answerText)
                                .font(.system(size: 12))
                                .textFieldStyle(.plain)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 8)
                                .background(.quaternary.opacity(0.5))
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                                .focused($isAnswerFocused)
                                .onSubmit {
                                    if !answerText.isEmpty {
                                        Task { await viewModel.decideRequest(request, decision: .answer, answer: answerText) }
                                    }
                                }

                            Button("Send") {
                                if !answerText.isEmpty {
                                    Task { await viewModel.decideRequest(request, decision: .answer, answer: answerText) }
                                }
                            }
                            .font(.system(size: 12, weight: .medium))
                            .disabled(answerText.isEmpty)
                        }
                    }
                }
            }

            // Buttons for permissions
            if request.kind == .permission {
                HStack(spacing: 8) {
                    Button("Deny") {
                        Task { await viewModel.decideRequest(request, decision: .deny) }
                    }
                    .font(.system(size: 12, weight: .medium))
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .tint(.red)

                    Button("Allow") {
                        Task { await viewModel.decideRequest(request, decision: .allow) }
                    }
                    .font(.system(size: 12, weight: .semibold))
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .keyboardShortcut(.defaultAction)

                    if request.always {
                        Button("Always") {
                            Task { await viewModel.decideRequest(request, decision: .always) }
                        }
                        .font(.system(size: 12, weight: .medium))
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .tint(.green)
                    }
                }
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(.quaternary.opacity(0.3))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(request.kind == .permission ? .orange.opacity(0.3) : .blue.opacity(0.3), lineWidth: 1)
                )
        )
        .padding(.horizontal, 12)
        .onAppear {
            if request.kind == .question && (request.multi || request.options.isEmpty) {
                isAnswerFocused = true
            }
        }
    }
}